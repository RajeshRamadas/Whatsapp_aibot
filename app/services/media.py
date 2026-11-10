"""Bounded, private WhatsApp media. All network/storage I/O runs in workers."""
import hashlib
import re
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx
from sqlalchemy import select

from ..config import settings
from ..db import Conversation, InboundEvent, Message, Session, now
from .channels import access_token
from .storage import storage

MIMES = {'image': {'image/jpeg', 'image/png'}, 'audio': {
    'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/wav', 'audio/amr', 'audio/aac'}, 'document': {
    'application/pdf', 'text/plain', 'application/msword', 'application/vnd.ms-excel',
    'application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation'}}


def validate(data, mime, kind):
    mime = mime.split(';')[0].strip().lower()
    if mime not in MIMES.get(kind, set()):
        raise ValueError('This attachment type is not supported')
    if not data or len(data) > min(settings.media_max_bytes, 5242880 if kind == 'image' else 16777216):
        raise ValueError('Attachment is empty or exceeds the size limit')
    signatures = {'image/png': data.startswith(b'\x89PNG\r\n\x1a\n'), 'image/jpeg': data.startswith(b'\xff\xd8\xff'),
                  'application/pdf': data.startswith(b'%PDF-'), 'audio/ogg': data.startswith(b'OggS'),
                  'audio/wav': data.startswith(b'RIFF') and data[8:12] == b'WAVE',
                  'audio/amr': data.startswith(b'#!AMR'), 'audio/mp4': data[4:8] == b'ftyp',
                  'audio/mpeg': data.startswith(b'ID3') or data[:1] == b'\xff',
                  'audio/aac': data[:1] == b'\xff'}
    if mime.startswith('application/vnd.openxmlformats'):
        signatures[mime] = data.startswith(b'PK\x03\x04')
    if mime in ('application/msword', 'application/vnd.ms-excel', 'application/vnd.ms-powerpoint'):
        signatures[mime] = data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
    if mime == 'text/plain':
        data.decode('utf-8')
        signatures[mime] = b'\x00' not in data
    if not signatures.get(mime, True):
        raise ValueError('Attachment content does not match its type')
    return mime


def filename(value):
    return re.sub(r'[\x00-\x1f\x7f]', '', str(value).replace('\\', '/').split('/')[-1])[:128] or 'attachment'


def key_for(tenant_id, identity):
    return f'tenant/{tenant_id}/media/{identity}'


def download(phone, tenant_id, identity):
    if not re.fullmatch(r'[0-9]{1,64}', str(identity)):
        raise ValueError('Invalid WhatsApp attachment')
    headers = {'Authorization': f'Bearer {access_token(phone, tenant_id)}'}
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        info = client.get(f'https://graph.facebook.com/{settings.whatsapp_graph_version}/{identity}',
                          params={'phone_number_id': phone}, headers=headers)
        info.raise_for_status()
        meta = info.json()
        url = urlsplit(meta.get('url', ''))
        if (url.scheme != 'https' or url.hostname != 'lookaside.fbsbx.com' or url.username or url.password
                or url.port not in (None, 443)):
            raise ValueError('Invalid WhatsApp download location')
        if int(meta.get('file_size', 0)) > settings.media_max_bytes:
            raise ValueError('Attachment exceeds the size limit')
        data = bytearray()
        with client.stream('GET', url.geturl(), headers=headers) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > settings.media_max_bytes:
                    raise ValueError('Attachment exceeds the size limit')
        digest = hashlib.sha256(data).hexdigest()
        if meta.get('sha256') and meta['sha256'] != digest:
            raise ValueError('Attachment checksum did not match')
        return bytes(data), meta['mime_type']


def prepare(tenant_id, parts):
    attachments = []
    for part in parts:
        if part.get('details'):
            attachments.append({'id': str(uuid5(NAMESPACE_URL, f'{tenant_id}:{part["id"]}')),
                                'kind': part['type'], 'status': 'ready', 'details': part['details']})
        if not part.get('media'):
            continue
        with Session() as db:
            event = db.scalar(select(InboundEvent).where(InboundEvent.tenant_id == tenant_id,
                              InboundEvent.channel_message_id == part['id']))
            cached = event.payload.get('attachments') if event else None
        if cached is None:
            item = part['media']
            identity = str(uuid5(NAMESPACE_URL, f'{tenant_id}:{part["id"]}'))
            cached = [{'id': identity, 'kind': part['type'], 'filename': filename(item.get('filename', part['type'])),
                       'status': 'ready', 'mime_type': '', 'size': 0}]
            attachment = cached[0]
            try:
                data, mime = download(part['phone_number_id'], tenant_id, item['id'])
                mime = validate(data, mime, part['type'])
                attachment.update(key=key_for(tenant_id, identity), mime_type=mime, size=len(data))
                storage().put(attachment['key'], data)
            except (ValueError, UnicodeError, KeyError):
                attachment['status'] = 'failed'
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (408, 429) or exc.response.status_code >= 500:
                    raise
                attachment['status'] = 'failed'
            with Session.begin() as db:
                event = db.scalar(select(InboundEvent).where(InboundEvent.tenant_id == tenant_id,
                                  InboundEvent.channel_message_id == part['id']))
                if event:
                    event.payload = {**event.payload, 'attachments': cached}
        attachments.extend(cached)
    return attachments


def send(phone, recipient, tenant_id, attachment, data, caption):
    """Stage privately, upload directly to Meta, then send its media ID once."""
    from ..whatsapp import graph_message
    storage().put(attachment['key'], data)
    attachment['status'] = 'ready'
    identity = upload(phone, tenant_id, attachment, data)
    content = {'id': identity}
    if caption:
        content['caption'] = caption
    if attachment['kind'] == 'document':
        content['filename'] = attachment['filename']
    result = graph_message(phone, {'to': recipient, 'type': attachment['kind'], attachment['kind']: content}, tenant_id)
    messages = result.json().get('messages') or []
    return messages[0].get('id') if messages else None


def upload(phone, tenant_id, attachment, data):
    response = httpx.post(f'https://graph.facebook.com/{settings.whatsapp_graph_version}/{phone}/media',
        headers={'Authorization': f'Bearer {access_token(phone, tenant_id)}'},
        data={'messaging_product': 'whatsapp', 'type': attachment['mime_type']},
        files={'file': (attachment['filename'], data, attachment['mime_type'])}, timeout=60)
    response.raise_for_status()
    return response.json()['id']


def expire():
    with Session() as db:
        rows = db.execute(select(Message, Conversation.tenant_id).join(Conversation).where(
            Message.created_at < now() - timedelta(days=settings.media_retention_days),
            Message.attachments != [], ~Message.attachments.bool_op('@>')([{'status': 'expired'}])).limit(100)).all()
    for message, tenant_id in rows:
        for item in message.attachments:
            if item.get('key') == key_for(tenant_id, item['id']):
                storage().delete(item['key'])
        with Session.begin() as db:
            row = db.scalar(select(Message).join(Conversation).where(Message.id == message.id,
                            Conversation.tenant_id == tenant_id).with_for_update(of=Message))
            if row:
                row.attachments = [{**item, 'status': 'expired'} for item in row.attachments]
                row.upload_data = None
