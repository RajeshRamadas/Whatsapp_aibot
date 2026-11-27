"""Private inbound Meta attachments. Provider URLs never reach messages or browsers."""
import asyncio
import mimetypes
from datetime import timedelta
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass
from uuid import NAMESPACE_URL, uuid5

import httpx
from sqlalchemy import select, text

from ..config import settings
from ..db import Channel, Conversation, Message, MetaDmEvent, Session, Tenant, now
from . import audio_validation, crypto, media, web_fetch
from .events import publish_sync


def snapshot(tenant_id, event_id, parts):
    result = []
    for index, part in enumerate(parts[:10]):
        part = part if isinstance(part, dict) else {}
        kind = {'image': 'image', 'audio': 'audio', 'video': 'video', 'file': 'document'}.get(
            part.get('type') if isinstance(part.get('type'), str) else '', 'unsupported')
        identity = str(uuid5(NAMESPACE_URL, f'meta:{tenant_id}:{event_id}:{index}'))
        item = {'id': identity, 'kind': kind}
        payload = part.get('payload')
        url = payload.get('url') if isinstance(payload, dict) else None
        if kind != 'unsupported' and isinstance(url, str) and 1 <= len(url) <= 2048:
            try:
                item['source_enc'] = crypto.encrypt_system('meta-media:' + tenant_id, identity, url.encode()).hex()
            except ValueError:
                pass  # Keep an unavailable attachment when encryption is not configured.
        result.append(item)
    if len(parts) > 10:
        result.append({'id': str(uuid5(NAMESPACE_URL, f'meta:{event_id}:overflow')), 'kind': 'unsupported'})
    return result


def placeholders(parts, tenant_id):
    return [{'id': item['id'], 'kind': item['kind'], 'filename': 'attachment',
             'key': media.key_for(tenant_id, item['id']),
             'status': 'queued' if item.get('source_enc') else 'failed'} for item in parts]


async def download(url, kind):
    """Only public Meta CDN HTTPS, pinned DNS/TLS, no redirects, cookies or bearer tokens."""
    async with asyncio.timeout(20):
        url = web_fetch.canonical(url)
        host = urlsplit(url).hostname
        if not any(host.endswith('.' + domain) for domain in ('fbcdn.net', 'fbsbx.com', 'cdninstagram.com')):
            raise ValueError('Unsupported attachment host')
        address = await asyncio.to_thread(web_fetch.resolve, host)
        if not web_fetch.public(address):
            raise ValueError('Unsafe attachment address')
        proxy = None if proxy_bypass(host) else getproxies().get('https', getproxies().get('all'))
        proxy = httpx.Proxy(proxy, ssl_context=httpx.create_ssl_context()
            if urlsplit(proxy).scheme == 'https' else None) if proxy else None
        transport = httpx.AsyncHTTPTransport(verify=web_fetch.PinnedTLS(host), proxy=proxy, trust_env=True, retries=0)
        limit = min(settings.media_max_bytes, 5242880 if kind == 'image' else 16777216)
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False) as client:
            async with client.stream('GET', httpx.URL(url).copy_with(host=address),
                headers={'Host': host, 'Accept-Encoding': 'identity'}, timeout=10) as response:
                if response.status_code != 200 or response.headers.get('content-encoding', 'identity') != 'identity':
                    raise ValueError('Attachment could not be downloaded')
                if int(response.headers.get('content-length', '0')) > limit:
                    raise ValueError('Attachment exceeds the size limit')
                data = bytearray()
                async for chunk in response.aiter_raw():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError('Attachment exceeds the size limit')
                mime = media.validate(bytes(data), response.headers.get('content-type', ''), kind)
                if kind == 'video' and await asyncio.to_thread(audio_validation.validate, bytes(data), mime) != 'video':
                    raise ValueError('Attachment must contain a supported video track')
                return bytes(data), mime


def bound(db, event):
    return db.scalar(select(Channel.id).where(Channel.id == event.channel_id, Channel.tenant_id == event.tenant_id,
        Channel.kind == event.channel, Channel.external_id == event.recipient_id)) is not None


def message_for(db, event):
    return db.scalar(select(Message).join(Conversation).where(Conversation.tenant_id == event.tenant_id,
        Conversation.channel == event.channel, Message.channel_message_id == event.provider_message_id,
        Message.role == 'user'))


def finish(event_id, claimed_at, attachments, *, cleanup=True):
    discarded, changed = True, None
    with Session.begin() as db:
        event = db.get(MetaDmEvent, event_id)
        tenant = db.get(Tenant, event.tenant_id, with_for_update=True) if event else None
        event = db.scalar(select(MetaDmEvent).where(MetaDmEvent.id == event_id)
            .with_for_update().execution_options(populate_existing=True))
        if event and event.status == 'media_processing' and event.media_claimed_at == claimed_at:
            message = message_for(db, event)
            if message:
                discarded = not (tenant and not tenant.suspended and bound(db, event)
                    and event.received_at >= now() - timedelta(days=settings.media_retention_days))
                message.attachments = ([{**item, 'status': 'failed'}
                    for item in placeholders(event.attachments, event.tenant_id)] if discarded else attachments)
                changed = event.tenant_id, message.conversation_id
            event.status, event.attachments, event.media_claimed_at = 'processed', [], None
    if discarded and cleanup:
        for item in attachments:
            if item.get('key'):
                media.storage().delete(item['key'])
    if changed:
        publish_sync(changed[0], 'message.updated', {'conversation_id': changed[1]})


def process_one():
    with Session.begin() as db:
        event = db.scalar(select(MetaDmEvent).where(MetaDmEvent.status == 'media_queued')
            .order_by(MetaDmEvent.received_at, MetaDmEvent.id).with_for_update(skip_locked=True).limit(1))
        if not event:
            return False
        event.status, event.media_claimed_at = 'media_processing', now()
        event_id, tenant_id, claimed_at = event.id, event.tenant_id, event.media_claimed_at
        tenant = db.get(Tenant, tenant_id)
        allowed = bool(tenant and not tenant.suspended and bound(db, event) and message_for(db, event)
                       and event.received_at >= now() - timedelta(days=settings.media_retention_days))
        parts, attachments = event.attachments, placeholders(event.attachments, tenant_id)
    for part, item in zip(parts, attachments, strict=True):
        if not allowed or not part.get('source_enc'):
            item['status'] = 'failed'
            continue
        try:
            url = crypto.decrypt_system('meta-media:' + tenant_id, item['id'],
                                        bytes.fromhex(part['source_enc'])).decode()
            data, mime = asyncio.run(download(url, item['kind']))
            item.update(key=media.key_for(tenant_id, item['id']), mime_type=mime, size=len(data),
                        filename='attachment' + (mimetypes.guess_extension(mime) or ''))
            media.storage().put(item['key'], data)
            item['status'] = 'ready'
        except Exception:
            # Signed URLs, provider errors and file contents must never enter logs.
            item['status'] = 'failed'
    finish(event_id, claimed_at, attachments)
    return True


def expire_stale():
    with Session() as db:
        stale = db.scalars(select(MetaDmEvent).where(MetaDmEvent.status == 'media_processing',
            MetaDmEvent.media_claimed_at < now() - timedelta(minutes=10)).limit(100)).all()
    for event in stale:
        finish(event.id, event.media_claimed_at,
               [{**item, 'status': 'failed'} for item in placeholders(event.attachments, event.tenant_id)],
               cleanup=False)
    with Session.begin() as db:
        expired = db.scalars(select(MetaDmEvent).where(text("attachments @? '$[*].source_enc'"),
            MetaDmEvent.received_at < now() - timedelta(days=settings.media_retention_days)).limit(100)).all()
        for event in expired:
            event.attachments = [{key: value for key, value in item.items() if key != 'source_enc'}
                                 for item in event.attachments]
