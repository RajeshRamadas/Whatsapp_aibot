"""Private attachment lifecycle, provider boundaries, authorization and safe dispatch."""
import asyncio
import hashlib
from datetime import timedelta
from weakref import WeakSet

import httpx
import pytest
from sqlalchemy import event, select

from app import whatsapp
from app.config import settings
from app.db import ApiKey, Conversation, Message, Session, now, uid
from app.jobs import worker
from app.services import media
from tests.conftest import wa_payload
from tests.test_whatsapp import link_number, post

PDF = b'%PDF-1.7\nfixture document'


def payload(kind='document', identity='wamid.media'):
    body = wa_payload(kind=kind, msg_id=identity)
    body['entry'][0]['changes'][0]['value']['messages'][0][kind] = {'id': '1234', 'filename': '../fixture.pdf'}
    return body


@pytest.fixture
def no_session_io():
    active = WeakSet()
    def begin(session, transaction, _connection):
        if transaction.parent is None:
            active.add(session)
    def end(session, transaction):
        if transaction.parent is None:
            active.discard(session)
    event.listen(Session, 'after_begin', begin)
    event.listen(Session, 'after_transaction_end', end)
    yield lambda: not active
    event.remove(Session, 'after_begin', begin)
    event.remove(Session, 'after_transaction_end', end)


def test_inbound_stores_private_media_and_hands_off(client, tenant, sent, monkeypatch, no_session_io):
    link_number(client, tenant)
    calls = []
    def download(*args):
        assert no_session_io()
        calls.append(args)
        return PDF, 'application/pdf'
    monkeypatch.setattr(media, 'download', download)
    assert post(client, payload()).status_code == 200
    assert post(client, payload()).status_code == 200
    [conv] = client.get('/api/v1/conversations', headers=tenant['headers']).json()
    assert conv['handoff'] and len(calls) == 1
    messages = client.get(f'/api/v1/conversations/{conv["id"]}/messages', headers=tenant['headers']).json()
    attachment = messages[0]['attachments'][0]
    assert attachment['filename'] == 'fixture.pdf' and attachment['status'] == 'ready'
    assert 'key' not in attachment and 'sha256' not in attachment
    url = f'/api/v1/conversations/{conv["id"]}/media/{attachment["id"]}'
    response = client.get(url, headers=tenant['headers'])
    assert response.content == PDF and response.headers['cache-control'] == 'private, no-store'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert client.get(url).status_code == 401
    assert client.get(url.replace(attachment['id'], 'bad'), headers=tenant['headers']).status_code == 422
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    assert client.get(url, headers={'X-Admin-Key': other['admin_key']}).status_code == 404
    assert sent and 'team' in sent[-1][2]


def test_worker_media_io_closes_sessions_and_preserves_debounce(client, tenant, sent, monkeypatch, no_session_io):
    link_number(client, tenant)
    async def no_delay(_):
        return None
    monkeypatch.setattr(worker.asyncio, 'sleep', no_delay)
    def download(*args):
        assert no_session_io()
        return PDF, 'application/pdf'
    monkeypatch.setattr(media, 'download', download)
    first = whatsapp.accept(whatsapp.parse_messages(payload())[0])
    second = whatsapp.accept(whatsapp.parse_messages(wa_payload('hours?', msg_id='wamid.caption'))[0])
    asyncio.run(worker.handle_message({}, first))
    asyncio.run(worker.handle_message({}, second))
    with Session() as db:
        rows = db.scalars(select(Message).where(Message.role == 'user')).all()
        assert len(rows) == 1 and rows[0].attachments and rows[0].content == 'hours?'
    assert len(sent) == 1


def test_agent_media_queues_once_and_dispatches_outside_sessions(client, tenant, monkeypatch, no_session_io):
    link_number(client, tenant)
    conv = whatsapp.conversation_for(tenant['tenant_id'], '15550001111')
    with Session.begin() as db:
        db.get(Conversation, conv.id).last_inbound_at = now()
    url = f'/api/v1/inbox/conversations/{conv.id}/media'
    headers = {**tenant['headers'], 'Idempotency-Key': 'media-one'}
    args = {'files': {'file': ('fixture.pdf', PDF, 'application/pdf')}, 'data': {'caption': 'Please review'}}
    assert client.post(url, **args).status_code == 401
    assert client.post(url, headers=tenant['headers'], **args).status_code == 422
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Read only', key_prefix='fixture',
                     key_hash=hashlib.sha256(b'fixture-viewer').hexdigest(), scopes=['viewer']))
    assert client.post(url, headers={'Authorization': 'Bearer fixture-viewer', 'Idempotency-Key': 'read'},
                       **args).status_code == 403
    first = client.post(url, headers=headers, **args)
    assert first.status_code == 202 and first.json()['status'] == 'queued'
    assert client.post(url, headers=headers, **args).json() == first.json()
    assert client.post(url, headers=headers, files=args['files'], data={'caption': 'Different'}).status_code == 409
    assert client.post(url.replace(conv.id, uid()), headers=headers, **args).status_code == 404
    assert client.post(url, headers={**headers, 'Idempotency-Key': 'bad'},
        files={'file': ('bad.png', PDF, 'image/png')}).status_code == 422
    calls = []
    def upload(url, **kwargs):
        assert no_session_io()
        assert url.endswith('/PNID1/media') and kwargs['files']['file'][1] == PDF
        calls.append('upload')
        return httpx.Response(200, json={'id': 'provider-media'}, request=httpx.Request('POST', url))
    def graph(phone, body, workspace):
        assert no_session_io()
        assert (phone, workspace) == ('PNID1', tenant['tenant_id'])
        assert body['document'] == {'id': 'provider-media', 'caption': 'Please review', 'filename': 'fixture.pdf'}
        calls.append('send')
        return httpx.Response(200, json={'messages': [{'id': 'wamid.out.media'}]})
    monkeypatch.setattr(media.httpx, 'post', upload)
    monkeypatch.setattr(whatsapp, 'graph_message', graph)
    assert worker.dispatch_agent_reply() and not worker.dispatch_agent_reply()
    assert calls == ['upload', 'send']
    with Session() as db:
        row = db.get(Message, first.json()['message_id'])
        assert row.upload_data is None and row.delivery_status == 'sent'
        assert row.attachments[0]['status'] == 'ready'
    uncertain = client.post(url, headers={**headers, 'Idempotency-Key': 'uncertain'}, **args)
    def timeout(*args):
        raise httpx.ReadTimeout('fixture')
    monkeypatch.setattr(whatsapp, 'graph_message', timeout)
    assert worker.dispatch_agent_reply() and not worker.dispatch_agent_reply()
    with Session() as db:
        row = db.get(Message, uncertain.json()['message_id'])
        assert row.delivery_status == 'unknown' and row.upload_data is None
        assert row.attachments[0]['status'] == 'ready'
    assert calls == ['upload', 'send', 'upload']
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Foreign media'}).json()
    assert client.post(url, headers={'X-Admin-Key': other['admin_key'], 'Idempotency-Key': 'foreign'},
                       **args).status_code == 404
    queued = client.post(url, headers={**headers, 'Idempotency-Key': 'before-window-expiry'}, **args)
    assert queued.status_code == 202
    with Session.begin() as db:
        db.get(Conversation, conv.id).last_inbound_at = now() - timedelta(days=2)
    assert worker.dispatch_agent_reply() and calls == ['upload', 'send', 'upload']
    with Session() as db:
        assert db.get(Message, queued.json()['message_id']).delivery_status == 'failed'
    assert client.post(url, headers={**headers, 'Idempotency-Key': 'expired'}, **args).status_code == 409


def test_private_download_url_and_size_checks(monkeypatch):
    responses = []
    def route(request):
        responses.append(request)
        if request.url.host == 'graph.facebook.com':
            return httpx.Response(200, json=metadata)
        return httpx.Response(200, content=PDF)
    metadata = {'url': 'https://lookaside.fbsbx.com/file', 'mime_type': 'application/pdf',
                'sha256': hashlib.sha256(PDF).hexdigest()}
    real_client = httpx.Client
    monkeypatch.setattr(media.httpx, 'Client', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(route)))
    monkeypatch.setattr(media, 'access_token', lambda *args: 'fake-channel-token')
    assert media.download('phone', 'tenant', '123') == (PDF, 'application/pdf')
    for url in ('http://lookaside.fbsbx.com/file', 'https://127.0.0.1/file',
                'https://lookaside.fbsbx.com.evil.test/file', 'https://user@lookaside.fbsbx.com/file'):
        metadata['url'] = url
        before = len(responses)
        with pytest.raises(ValueError):
            media.download('phone', 'tenant', '123')
        assert len(responses) == before + 1
    metadata['url'] = 'https://lookaside.fbsbx.com/file'
    metadata['sha256'] = 'bad'
    with pytest.raises(ValueError):
        media.download('phone', 'tenant', '123')
    metadata['file_size'] = settings.media_max_bytes + 1
    with pytest.raises(ValueError):
        media.download('phone', 'tenant', '123')
    with pytest.raises(ValueError):
        media.download('phone', 'tenant', '../secret')


def test_media_expiry_and_structured_inbound(client, tenant, sent, monkeypatch):
    link_number(client, tenant)
    monkeypatch.setattr(media, 'download', lambda *args: (PDF, 'application/pdf'))
    post(client, payload())
    with Session.begin() as db:
        row = db.scalar(select(Message).where(Message.role == 'user'))
        row.created_at = now() - timedelta(days=settings.media_retention_days + 1)
        key = row.attachments[0]['key']
        path = media.storage()._path(key)
    media.expire()
    assert not path.exists()
    body = payload('location', 'wamid.location')
    body['entry'][0]['changes'][0]['value']['messages'][0]['location'] = {'latitude': 12.97, 'longitude': 77.59}
    post(client, body)
    with Session() as db:
        rows = db.scalars(select(Message).where(Message.role == 'user').order_by(Message.id)).all()
        assert rows[0].attachments[0]['status'] == 'expired'
        assert rows[-1].attachments[0]['details'] == {'latitude': 12.97, 'longitude': 77.59}


def test_expired_meta_file_still_appears_in_the_inbox(client, tenant, sent, monkeypatch):
    link_number(client, tenant)
    def gone(*args):
        raise httpx.HTTPStatusError('fixture', request=httpx.Request('GET', 'https://graph.facebook.com/123'),
                                    response=httpx.Response(404))
    monkeypatch.setattr(media, 'download', gone)
    assert post(client, payload()).status_code == 200
    with Session() as db:
        assert db.scalar(select(Message).where(Message.role == 'user')).attachments[0]['status'] == 'failed'
