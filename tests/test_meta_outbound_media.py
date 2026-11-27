"""Meta media replies: private staging, ownership, FIFO, receipts and uncertain delivery."""
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import meta_dm_connections
from app.config import settings
from app.db import ApiKey, Channel, Conversation, Message, Session, Tenant, now
from app.services import media, meta_dm_replies, meta_outbound_media, meta_receipts
from app.services.crypto import encrypt
from app.services.meta_signup import MetaSignupError
from tests import test_media
from tests.meta_gif_fixture import gif_bytes
from tests.test_meta_dm_replies import setup_conversation
from tests.test_meta_receipts import data as receipt

no_session_io = test_media.no_session_io
PDF = b'%PDF-1.7\nprivate agent attachment'


@pytest.fixture
def account(tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    for target in ('app.api.media.publish_sync', 'app.api.inbox.publish_sync',
                   'app.services.meta_dm_replies.publish_sync', 'app.services.meta_receipts.publish_sync'):
        monkeypatch.setattr(target, lambda *args: None)
    channel, conv = setup_conversation(tenant['tenant_id'])
    secret = encrypt(tenant['tenant_id'], 'fake-page-token')
    with Session.begin() as db:
        db.get(Channel, channel).credentials_enc = secret
    return channel, conv


def queue(client, tenant, conv, key='one', **kwargs):
    return client.post(f'/api/v1/inbox/conversations/{conv}/media',
        headers={**tenant['headers'], 'Idempotency-Key': key},
        files={'file': ('निजी.pdf', PDF, 'application/pdf')}, **kwargs)


@pytest.mark.parametrize('kind', ['instagram', 'messenger'])
def test_queue_private_delivery_access_and_expiry(client, tenant, account, monkeypatch, kind, no_session_io):
    channel, conv = account
    with Session.begin() as db:
        db.get(Channel, channel).kind = db.get(Conversation, conv).channel = kind
        db.add(Message(conversation_id=conv, role='assistant', direction='outbound', content='stale bot draft',
                       delivery_status='queued'))
    response = queue(client, tenant, conv)
    assert response.status_code == 202 and queue(client, tenant, conv).json() == response.json()
    identity = response.json()['message_id']
    assert queue(client, tenant, conv, data={'caption': 'changed'}).status_code == 409
    with Session() as db:
        assert db.get(Conversation, conv).handoff and db.get(Conversation, conv).status == 'agent'
        assert db.scalar(select(Message).where(Message.content == 'stale bot draft')).delivery_status == 'failed'
    calls = []
    def send(channel, recipient, item, data):
        assert no_session_io() and media.storage().get(item['key']) == PDF
        assert (channel.kind, recipient, data) == (kind, '987654', PDF)
        calls.append(item['filename'])
        assert not meta_dm_replies.dispatch_one()
        with pytest.raises(HTTPException) as exc:
            meta_dm_connections.disconnect(channel.id, SimpleNamespace(tenant=SimpleNamespace(id=tenant['tenant_id'])))
        assert exc.value.status_code == 409
        return 'mid-1'
    monkeypatch.setattr(meta_outbound_media, 'send', send)
    assert meta_dm_replies.dispatch_one() and not meta_dm_replies.dispatch_one()
    assert calls == ['निजी.pdf']
    with Session.begin() as db:
        row = db.get(Message, identity)
        assert row.delivery_status == 'sent' and row.upload_data is None and row.meta_acknowledged_at
        row.meta_acknowledged_at = now() - timedelta(seconds=2)
        row.dispatch_started_at = now() - timedelta(seconds=3)
        attachment = row.attachments[0]
        last_inbound = db.get(Conversation, conv).last_inbound_at
    assert meta_receipts.store(receipt(kind, 'read', watermark=int(now().timestamp() * 1000))) == 1
    assert meta_receipts.process_one()
    with Session() as db:
        assert db.get(Message, identity).delivery_status == 'read'
        assert db.get(Conversation, conv).last_inbound_at == last_inbound
    path = f'/api/v1/conversations/{conv}/media/{attachment["id"]}'
    assert client.get(path, headers=tenant['headers']).content == PDF
    assert client.get(path).status_code == 401
    with Session.begin() as db:
        db.get(Message, identity).created_at = now() - timedelta(days=settings.media_retention_days + 1)
    media.expire()
    assert client.get(path, headers=tenant['headers']).status_code == 404
    assert not media.storage()._path(attachment['key']).exists()


def test_api_auth_isolation_types_and_windows(client, tenant, account):
    channel, conv = account
    path = f'/api/v1/inbox/conversations/{conv}/media'
    args = {'files': {'file': ('file.pdf', PDF, 'application/pdf')}}
    assert client.post(path, **args).status_code == 401
    assert client.post(path, headers=tenant['headers'], **args).status_code == 422
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Viewer', key_prefix='viewer',
            key_hash=hashlib.sha256(b'meta-media-viewer').hexdigest(), scopes=['viewer']))
    assert client.post(path, headers={'Authorization': 'Bearer meta-media-viewer', 'Idempotency-Key': 'v'},
                       **args).status_code == 403
    foreign = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                          json={'name': 'Foreign'}).json()
    assert client.post(path, headers={'X-Admin-Key': foreign['admin_key'], 'Idempotency-Key': 'f'},
                       **args).status_code == 404
    assert client.post(path.replace(conv, 'bad'), headers={**tenant['headers'], 'Idempotency-Key': 'x'},
                       **args).status_code == 422
    assert queue(client, tenant, conv, data={'caption': 'Not a supported caption'}).status_code == 422
    assert client.post(path, headers={**tenant['headers'], 'Idempotency-Key': 'text'},
        files={'file': ('file.txt', b'unsupported', 'text/plain')}).status_code == 422
    assert client.post(path, headers={**tenant['headers'], 'Idempotency-Key': 'fake'},
        files={'file': ('file.png', PDF, 'image/png')}).status_code == 422
    with Session.begin() as db:
        db.get(Channel, channel).credentials_enc = None
    assert queue(client, tenant, conv).status_code == 503
    with Session.begin() as db:
        db.get(Conversation, conv).last_inbound_at = now() - timedelta(hours=25)
    assert queue(client, tenant, conv).status_code == 409


@pytest.mark.parametrize('kind', ['instagram', 'messenger', 'whatsapp'])
def test_gif_reply_uses_private_staging_only_for_meta(client, tenant, account, monkeypatch, kind):
    channel, conv = account
    with Session.begin() as db:
        db.get(Channel, channel).kind = db.get(Conversation, conv).channel = kind
    path = f'/api/v1/inbox/conversations/{conv}/media'
    headers = {**tenant['headers'], 'Idempotency-Key': 'gif'}
    gif = gif_bytes()
    assert client.post(path, headers=headers, files={'file': ('bad.gif', b'GIF89a;', 'image/gif')}).status_code == 422
    result = client.post(path, headers=headers, files={'file': ('reply.gif', gif, 'image/gif')})
    if kind == 'whatsapp':
        assert result.status_code == 422
        return
    assert result.status_code == 202

    def send(channel, recipient, item, data):
        assert (channel.kind, item['mime_type'], item['kind'], data) == (kind, 'image/gif', 'image', gif)
        assert media.storage().get(item['key']) == gif
        return 'sent-gif'

    monkeypatch.setattr(meta_outbound_media, 'send', send)
    assert meta_dm_replies.dispatch_one()
    with Session() as db:
        row = db.get(Message, result.json()['message_id'])
        assert row.delivery_status == 'sent'
        url = f'/api/v1/conversations/{conv}/media/{row.attachments[0]["id"]}'
    assert client.get(url).status_code == 401
    response = client.get(url, headers=tenant['headers'])
    assert response.content == gif and response.headers['content-type'] == 'image/gif'


@pytest.mark.parametrize('change', ['closed', 'suspended', 'disconnected', 'hand_back', 'window', 'stale', 'storage'])
def test_rechecks_after_staging_and_clears_temporary_bytes(client, tenant, account, monkeypatch, change):
    channel, conv = account
    identity = queue(client, tenant, conv).json()['message_id']
    stage, calls = meta_outbound_media.stage, []
    def staged(*args):
        stage(*args)
        with Session.begin() as db:
            row = db.get(Conversation, conv)
            if change == 'closed':
                row.status = 'closed'
            elif change == 'suspended':
                db.get(Tenant, tenant['tenant_id']).suspended = True
            elif change == 'disconnected':
                db.delete(db.get(Channel, channel))
            elif change == 'hand_back':
                row.status, row.handoff = 'bot', False
            elif change == 'window':
                row.last_inbound_at = now() - timedelta(hours=25)
            elif change == 'stale':
                db.get(Message, identity).dispatch_started_at = now() - timedelta(minutes=3)
        if change == 'stale':
            meta_dm_replies.expire_stale()
        if change == 'storage':
            raise OSError('Private storage details')
    monkeypatch.setattr(meta_outbound_media, 'stage', staged)
    monkeypatch.setattr(meta_outbound_media, 'send', lambda *args: calls.append(args))
    assert meta_dm_replies.dispatch_one() and not meta_dm_replies.dispatch_one()
    assert calls == []
    with Session() as db:
        row = db.get(Message, identity)
        assert row.delivery_status == ('unknown' if change == 'stale' else 'failed') and row.upload_data is None


def test_mixed_fifo_and_unknown_sends_are_never_replayed(client, tenant, account, monkeypatch, no_session_io):
    _, conv = account
    first = queue(client, tenant, conv).json()['message_id']
    path = f'/api/v1/inbox/conversations/{conv}/reply'
    assert client.post(path, headers=tenant['headers'], json={'message': 'Following text'}).status_code == 200
    calls = []
    def uncertain(*args):
        assert no_session_io() and not meta_dm_replies.dispatch_one()
        calls.append('file')
        raise MetaSignupError('Synthetic timeout')
    def text(*args):
        assert no_session_io()
        calls.append(args[-1])
        return 'text-mid'
    monkeypatch.setattr(meta_outbound_media, 'send', uncertain)
    monkeypatch.setattr(meta_dm_replies, 'send', text)
    assert meta_dm_replies.dispatch_one() and meta_dm_replies.dispatch_one() and not meta_dm_replies.dispatch_one()
    assert calls == ['file', 'Following text']
    assert queue(client, tenant, conv).json() == {'status': 'unknown', 'message_id': first}
    assert not meta_dm_replies.dispatch_one()
    with Session() as db:
        assert db.get(Message, first).upload_data is None and db.get(Message, first).attachments[0]['status'] == 'ready'


def test_crashed_send_clears_temporary_bytes_without_replay(client, tenant, account):
    _, conv = account
    identity = queue(client, tenant, conv).json()['message_id']
    with Session.begin() as db:
        row = db.get(Message, identity)
        row.delivery_status, row.dispatch_started_at = 'sending', now() - timedelta(minutes=3)
    meta_dm_replies.expire_stale()
    assert not meta_dm_replies.dispatch_one()
    with Session() as db:
        row = db.get(Message, identity)
        assert row.delivery_status == 'unknown' and row.upload_data is None
        assert row.attachments[0]['status'] == 'failed'


@pytest.mark.parametrize('scenario', ['ok', 'redirect', 'timeout', 'recipient', 'missing',
                                     'too_large', 'error', 'compressed'])
def test_one_multipart_request_and_private_provider_errors(client, tenant, account, monkeypatch, scenario,
                                                          no_session_io):
    channel_id, _ = account
    with Session() as db:
        channel = db.get(Channel, channel_id)
    calls = []
    item = {'kind': 'document', 'mime_type': 'application/pdf', 'filename': 'निजी.pdf'}
    def route(request):
        assert no_session_io() and request.url.path.endswith('/me/messages') and request.url.query == b''
        assert request.headers['authorization'] == 'Bearer fake-page-token'
        assert PDF in request.content and b'filename="attachment.pdf"' in request.content
        assert b'"type": "file"' in request.content and b'"id": "987654"' in request.content
        calls.append(request)
        if scenario == 'timeout':
            raise httpx.ReadTimeout('private-provider-body')
        if scenario == 'redirect':
            return httpx.Response(302, headers={'location': 'https://other.test/token'})
        if scenario == 'compressed':
            return httpx.Response(200, headers={'content-encoding': 'gzip'}, stream=httpx.ByteStream(b'compressed'))
        body = {'message_id': 'sent-mid', 'recipient_id': 'wrong' if scenario == 'recipient' else '987654'}
        if scenario == 'missing':
            body.pop('message_id')
        if scenario == 'error':
            body['error'] = {'message': 'private-provider-body'}
        return httpx.Response(200, content=b'x' * 5000 if scenario == 'too_large' else json.dumps(body).encode())
    real_client = httpx.Client
    monkeypatch.setattr(meta_outbound_media.httpx, 'Client', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(route)))
    if scenario == 'ok':
        assert meta_outbound_media.send(channel, '987654', item, PDF) == 'sent-mid'
    else:
        with pytest.raises(MetaSignupError) as exc:
            meta_outbound_media.send(channel, '987654', item, PDF)
        assert 'private-provider-body' not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize(('mime', 'body', 'suffix'), [('image/png', b'\x89PNG\r\n\x1a\nfixture', '.png'),
                                                    ('image/jpeg', b'\xff\xd8\xfffixture', '.jpg'),
                                                    ('image/gif', gif_bytes(), '.gif')])
def test_multipart_image_types(client, tenant, account, monkeypatch, mime, body, suffix):
    with Session() as db:
        channel = db.get(Channel, account[0])
    def route(request):
        assert body in request.content and b'"type": "image"' in request.content
        assert ('filename="attachment' + suffix + '"').encode() in request.content
        return httpx.Response(200, json={'message_id': 'image-mid', 'recipient_id': '987654'})
    real_client = httpx.Client
    monkeypatch.setattr(meta_outbound_media.httpx, 'Client', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(route)))
    assert meta_outbound_media.send(channel, '987654', {'kind': 'image', 'mime_type': mime}, body) == 'image-mid'
