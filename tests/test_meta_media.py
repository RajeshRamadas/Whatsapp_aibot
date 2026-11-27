"""Signed Meta attachments, private downloads, worker fencing and hostile CDN responses."""
import asyncio
import base64
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db import Channel, Conversation, Message, MetaDmEvent, Session, Tenant, now, uid
from app.models.meta_bot import MetaBotTurn
from app.services import audio_validation, media, meta_media
from app.services.meta_dm import process_one
from tests import test_media
from tests import test_meta_audio_replies as audio_fixtures
from tests import test_meta_video_replies as video_fixtures
from tests.test_meta_dm import signed

PDF = b'%PDF-1.7\nprivate Meta document'
VIDEO = (Path(__file__).resolve().parents[1] / 'web/e2e/fixtures/meta-video.mp4').read_bytes()
SOURCE = 'https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=123&signature=private-url'
no_session_io = test_media.no_session_io


def incoming(kind='messenger', mid='media-one', parts=None):
    return {'object': 'page' if kind == 'messenger' else kind, 'entry': [{'messaging': [{
        'sender': {'id': 'customer'}, 'recipient': {'id': '123'}, 'timestamp': int(now().timestamp() * 1000),
        'message': {'mid': mid, 'text': 'Please review', 'attachments': parts if parts is not None else [
            {'type': 'file', 'payload': {'url': SOURCE}}]}}]}]}


@pytest.fixture
def account(tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b'm' * 32).decode())
    monkeypatch.setattr('app.services.meta_dm.publish_sync', lambda *args: None)
    monkeypatch.setattr(meta_media, 'publish_sync', lambda *args: None)
    with Session.begin() as db:
        channel = Channel(id=uid(), tenant_id=tenant['tenant_id'], kind='messenger', external_id='123',
                          meta_bot_enabled=True)
        db.add(channel)
        return channel.id


def accept(client, body):
    raw, headers = signed(body, settings.whatsapp_app_secret)
    assert client.post('/webhooks/meta-dm', content=raw, headers=headers).status_code == 200


@pytest.mark.parametrize('kind', ['instagram', 'messenger'])
@pytest.mark.parametrize('attachment', ['document', 'video'])
def test_inbound_private_media_once_and_staff_handoff(client, tenant, account, kind, attachment,
                                                    monkeypatch, no_session_io, chats):
    with Session.begin() as db:
        db.get(Channel, account).kind = kind
    calls = []
    data, mime = (VIDEO, 'video/mp4') if attachment == 'video' else (PDF, 'application/pdf')
    async def download(url, kind):
        assert no_session_io() and (url, kind) == (SOURCE, attachment)
        calls.append(url)
        return data, mime
    monkeypatch.setattr(meta_media, 'download', download)
    body = incoming(kind, parts=[{'type': 'video' if attachment == 'video' else 'file', 'payload': {'url': SOURCE}}])
    assert client.post('/webhooks/meta-dm', json=body).status_code == 401
    accept(client, body)
    accept(client, body)
    assert not calls  # Neither webhook nor intake performs HTTP/storage I/O.
    with Session() as db:
        event = db.scalar(select(MetaDmEvent))
        assert db.query(MetaDmEvent).count() == 1
        assert SOURCE not in json.dumps(event.attachments) and event.attachments[0]['source_enc']
    assert process_one() and not process_one()
    with Session() as db:
        conv = db.scalar(select(Conversation))
        assert conv.status == 'waiting' and conv.handoff
        assert not db.scalar(select(MetaBotTurn))
        assert len(db.scalars(select(Message).where(Message.role == 'assistant')).all()) == 1
        conversation_id = conv.id
    messages_url = f'/api/v1/conversations/{conversation_id}/messages'
    queued = client.get(messages_url, headers=tenant['headers']).json()[0]['attachments'][0]
    assert queued['status'] == 'queued' and queued['kind'] == attachment
    assert meta_media.process_one() and not meta_media.process_one()
    response = client.get(messages_url, headers=tenant['headers'])
    item = response.json()[0]['attachments'][0]
    assert item['status'] == 'ready' and item['mime_type'] == mime
    assert 'source_enc' not in response.text and 'key' not in item and SOURCE not in response.text
    url = f'/api/v1/conversations/{conversation_id}/media/{item["id"]}'
    result = client.get(url, headers=tenant['headers'])
    assert result.content == data and result.headers['cache-control'] == 'private, no-store'
    if attachment == 'video':
        assert result.headers['content-disposition'].startswith('inline;')
        partial = client.get(url, headers={**tenant['headers'], 'Range': 'bytes=0-15'})
        assert partial.status_code == 206 and partial.content == VIDEO[:16]
    assert client.get(url).status_code == 401
    assert client.get(url.replace(item['id'], 'bad'), headers=tenant['headers']).status_code == 422
    foreign = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                          json={'name': 'Other'}).json()
    assert client.get(url, headers={'X-Admin-Key': foreign['admin_key']}).status_code == 404
    with Session.begin() as db:
        event = db.scalar(select(MetaDmEvent))
        assert event.status == 'processed' and event.attachments == []
        row = db.scalar(select(Message).where(Message.role == 'user'))
        row.created_at = now() - timedelta(days=settings.media_retention_days + 1)
        path = media.storage()._path(row.attachments[0]['key'])
    media.expire()
    assert not path.exists() and client.get(url, headers=tenant['headers']).status_code == 404
    assert calls == [SOURCE]
    assert all(not fake.calls for fake in chats.values())


@pytest.mark.parametrize('attachment', ['file', 'video'])
def test_attachment_cancels_pending_bot_work(client, tenant, account, attachment):
    body = incoming(mid='text-first')
    del body['entry'][0]['messaging'][0]['message']['attachments']
    accept(client, body)
    assert process_one()
    with Session.begin() as db:
        conv = db.scalar(select(Conversation))
        assert db.scalar(select(MetaBotTurn)).status == 'queued'
        db.add(Message(conversation_id=conv.id, role='assistant', direction='outbound',
                       content='stale draft', delivery_status='queued'))
    accept(client, incoming(parts=[{'type': attachment, 'payload': {'url': SOURCE}}]))
    assert process_one()
    with Session() as db:
        assert db.scalar(select(MetaBotTurn)).status == 'cancelled'
        draft = db.scalar(select(Message).where(Message.content == 'stale draft'))
        assert draft.delivery_status == 'failed' and db.scalar(select(Conversation)).handoff


@pytest.mark.parametrize('change', ['disconnect', 'suspend', 'stale', 'deleted', 'expired', 'provider_failure'])
@pytest.mark.parametrize('attachment', ['document', 'video'])
def test_download_completion_is_fenced(client, tenant, account, monkeypatch, change, attachment):
    accept(client, incoming(parts=[{'type': 'video' if attachment == 'video' else 'file', 'payload': {'url': SOURCE}}]))
    assert process_one()
    paths = []
    async def download(*args):
        with Session.begin() as db:
            event = db.scalar(select(MetaDmEvent))
            row = db.scalar(select(Message).where(Message.role == 'user'))
            paths.append(media.storage()._path(row.attachments[0]['key']))
            if change == 'disconnect':
                db.delete(db.get(Channel, account))
            elif change == 'suspend':
                db.get(Tenant, tenant['tenant_id']).suspended = True
            elif change == 'stale':
                event.media_claimed_at = now() - timedelta(minutes=11)
            elif change == 'deleted':
                db.delete(row)
            elif change == 'expired':
                event.received_at = now() - timedelta(days=settings.media_retention_days + 1)
        if change == 'stale':
            meta_media.expire_stale()
        if change == 'provider_failure':
            raise httpx.ReadTimeout('synthetic secret URL')
        return (VIDEO, 'video/mp4') if attachment == 'video' else (PDF, 'application/pdf')
    monkeypatch.setattr(meta_media, 'download', download)
    assert meta_media.process_one() and not meta_media.process_one()
    with Session() as db:
        event = db.scalar(select(MetaDmEvent))
        assert event.attachments == [] and event.status == 'processed'
        row = db.scalar(select(Message).where(Message.role == 'user'))
        assert not row or row.attachments[0]['status'] == 'failed'
    assert all(not path.exists() for path in paths)


def test_unsupported_missing_and_bounded_attachments_are_visible(client, tenant, account, monkeypatch):
    calls = []
    async def unexpected(*args):
        calls.append(args)
        raise AssertionError('No supported URL should be downloaded')
    monkeypatch.setattr(meta_media, 'download', unexpected)
    parts = [{'type': []}, 'bad', {'type': 'unknown', 'payload': {'url': SOURCE}}, {'type': 'audio'}] * 3
    body = incoming(parts=parts)
    del body['entry'][0]['messaging'][0]['message']['text']
    accept(client, body)
    assert process_one() and meta_media.process_one()
    with Session() as db:
        items = db.scalar(select(Message).where(Message.role == 'user')).attachments
        assert len(items) == 11 and all(item['status'] == 'failed' for item in items)
        assert db.scalar(select(Conversation)).handoff
    assert calls == []


def test_old_and_disconnected_events_never_download(client, tenant, account, monkeypatch):
    calls = []
    async def unexpected(*args):
        calls.append(args)
        raise AssertionError('Expired or disconnected sources must not be fetched')
    monkeypatch.setattr(meta_media, 'download', unexpected)
    accept(client, incoming())
    with Session.begin() as db:
        db.scalar(select(MetaDmEvent)).received_at = now() - timedelta(days=settings.media_retention_days + 1)
    meta_media.expire_stale()
    with Session() as db:
        assert 'source_enc' not in json.dumps(db.scalar(select(MetaDmEvent)).attachments)
    assert process_one() and meta_media.process_one()
    accept(client, incoming(mid='before-disconnect'))
    with Session.begin() as db:
        db.delete(db.get(Channel, account))
    assert process_one() and not meta_media.process_one()
    with Session() as db:
        event = db.scalar(select(MetaDmEvent).where(MetaDmEvent.provider_message_id == 'messenger:before-disconnect'))
        assert event.status == 'ignored' and event.attachments == []
    assert calls == []


def test_stale_sweep_cannot_delete_a_concurrently_completed_download(client, tenant, account, monkeypatch):
    accept(client, incoming())
    assert process_one()
    claimed_at = now() - timedelta(minutes=11)
    with Session.begin() as db:
        event = db.scalar(select(MetaDmEvent))
        event.status, event.media_claimed_at = 'media_processing', claimed_at
        identity = event.id
        item = db.scalar(select(Message).where(Message.role == 'user')).attachments[0]
    media.storage().put(item['key'], PDF)
    completed = {**item, 'status': 'ready', 'mime_type': 'application/pdf', 'size': len(PDF)}
    finish = meta_media.finish
    def concurrent_finish(event_id, claim, attachments, **kwargs):
        finish(identity, claimed_at, [completed])
        finish(event_id, claim, attachments, **kwargs)
    monkeypatch.setattr(meta_media, 'finish', concurrent_finish)
    meta_media.expire_stale()
    with Session() as db:
        assert db.scalar(select(Message).where(Message.role == 'user')).attachments[0]['status'] == 'ready'
    assert media.storage().get(item['key']) == PDF


@pytest.mark.parametrize('scenario', ['ok', 'redirect', 'compressed', 'length', 'stream', 'mime', 'signature',
                                     'private', 'untrusted', 'credentials', 'scheme', 'dns_private'])
@pytest.mark.parametrize('kind', ['document', 'video'])
def test_pinned_cdn_download_bounds(monkeypatch, scenario, kind):
    calls = []
    body = VIDEO if kind == 'video' else PDF
    mime = 'video/mp4' if kind == 'video' else 'application/pdf'
    headers = {'content-type': mime}
    status = 200
    source = SOURCE
    if scenario == 'redirect':
        status, headers = 302, {'location': 'https://127.0.0.1/secret'}
    elif scenario == 'compressed':
        headers['content-encoding'] = 'gzip'
    elif scenario == 'length':
        headers['content-length'] = str(settings.media_max_bytes + 1)
    elif scenario == 'stream':
        monkeypatch.setattr(settings, 'media_max_bytes', 10)
    elif scenario == 'mime':
        headers['content-type'] = 'text/html'
    elif scenario == 'signature':
        body = b'<script>invalid PDF</script>'
    elif scenario == 'private':
        source = 'https://127.0.0.1/file'
    elif scenario == 'untrusted':
        source = 'https://fbcdn.net.evil.test/file'
    elif scenario == 'credentials':
        source = 'https://user:pass@lookaside.fbsbx.com/file'
    elif scenario == 'scheme':
        source = 'http://lookaside.fbsbx.com/file'
    monkeypatch.setattr(meta_media.web_fetch, 'resolve',
                        lambda _: '127.0.0.1' if scenario == 'dns_private' else '8.8.8.8')
    def route(request):
        assert request.url.host == '8.8.8.8' and request.headers['host'] == 'lookaside.fbsbx.com'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        calls.append(request)
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))
    def transport(**kwargs):
        assert kwargs['verify'].hostname == 'lookaside.fbsbx.com' and kwargs['retries'] == 0
        return httpx.MockTransport(route)
    monkeypatch.setattr(meta_media.httpx, 'AsyncHTTPTransport', transport)
    if scenario == 'ok':
        assert asyncio.run(meta_media.download(source, kind)) == (body, mime)
    else:
        with pytest.raises(ValueError):
            asyncio.run(meta_media.download(source, kind))
    assert len(calls) == (0 if scenario in ('private', 'untrusted', 'credentials', 'scheme', 'dns_private') else 1)


@pytest.mark.parametrize(('kind', 'mime', 'body'), [('image', 'image/png', b'\x89PNG\r\n\x1a\nfixture'),
                                                   ('audio', 'audio/ogg', b'OggSfixture')])
def test_image_and_audio_cdn_download(monkeypatch, kind, mime, body):
    monkeypatch.setattr(meta_media.web_fetch, 'resolve', lambda _: '8.8.8.8')
    monkeypatch.setattr(meta_media.httpx, 'AsyncHTTPTransport', lambda **kwargs: httpx.MockTransport(
        lambda request: httpx.Response(200, headers={'content-type': mime}, stream=httpx.ByteStream(body))))
    assert asyncio.run(meta_media.download(SOURCE, kind)) == (body, mime)


@pytest.mark.parametrize('case', ['valid', 'audio_only', 'fake', 'codec', 'mime', 'unavailable', 'timeout'])
def test_video_cdn_validation_before_private_publication(client, tenant, account, monkeypatch,
                                                       no_session_io, chats, case):
    data = (video_fixtures.clip(audio='aac') if case == 'valid' else
            audio_fixtures.encoded('m4a') if case == 'audio_only' else
            video_fixtures.clip('mpeg4') if case == 'codec' else
            b'\0\0\0\x18ftypfixture' if case == 'fake' else VIDEO)
    mime = 'video/webm' if case == 'mime' else 'video/mp4'
    calls, validator = [], audio_validation.validate
    def route(request):
        assert no_session_io() and request.url.host == '8.8.8.8'
        assert request.headers['host'] == 'lookaside.fbsbx.com'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        calls.append(request)
        return httpx.Response(200, headers={'content-type': mime}, stream=httpx.ByteStream(data))
    def validate(*args):
        assert no_session_io()
        if case == 'unavailable':
            raise audio_validation.Unavailable('Synthetic missing decoder')
        if case == 'timeout':
            raise TimeoutError('Synthetic decoder timeout')
        return validator(*args)
    monkeypatch.setattr(meta_media.web_fetch, 'resolve', lambda _: '8.8.8.8')
    monkeypatch.setattr(meta_media.httpx, 'AsyncHTTPTransport', lambda **kwargs: httpx.MockTransport(route))
    monkeypatch.setattr(audio_validation, 'validate', validate)
    accept(client, incoming(parts=[{'type': 'video', 'payload': {'url': SOURCE}}]))
    assert process_one() and meta_media.process_one() and not meta_media.process_one()
    with Session() as db:
        message = db.scalar(select(Message).where(Message.role == 'user'))
        item = message.attachments[0]
        assert item['kind'] == 'video' and item['status'] == ('ready' if case == 'valid' else 'failed')
        assert media.storage()._path(item['key']).exists() == (case == 'valid')
        if case == 'valid':
            assert item['mime_type'] == 'video/mp4' and item['filename'] == 'attachment.mp4'
            assert media.storage().get(item['key']) == data
        assert db.scalar(select(MetaDmEvent)).attachments == []
        assert db.scalar(select(Conversation)).handoff and not db.scalar(select(MetaBotTurn))
    assert len(calls) == 1 and all(not fake.calls for fake in chats.values())
