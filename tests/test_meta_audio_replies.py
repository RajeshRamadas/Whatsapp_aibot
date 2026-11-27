"""Manual Meta audio uses private delivery and receipts without speech interpretation."""
import hashlib
import io
import json
import subprocess
import wave
from contextlib import contextmanager
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db import ApiKey, Channel, Conversation, Message, Session, now
from app.models.meta_receipts import MetaReceipt
from app.services import media, meta_dm_replies, meta_outbound_media, meta_receipts
from app.services.meta_signup import MetaSignupError
from tests import test_meta_outbound_media
from tests.test_meta_receipts import data as receipt

account = test_meta_outbound_media.account
no_session_io = test_meta_outbound_media.no_session_io


def wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        writer.writeframes(b'\0\0' * 16000)
    return output.getvalue()


@lru_cache
def encoded(format_name):
    with TemporaryDirectory() as directory:
        path = Path(directory) / ('sample.' + format_name)
        args = (['-f', 'lavfi', '-i', 'color=s=16x16:r=10', '-an', '-c:v', 'mpeg4'] if format_name == 'mp4'
                else ['-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono', '-c:a', 'aac'])
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', *args, '-t', '0.2', '-y', str(path)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
        return path.read_bytes()


@pytest.mark.parametrize('kind', ['instagram', 'messenger'])
def test_audio_queue_wire_delivery_receipts_and_private_access(client, tenant, account, monkeypatch, kind,
                                                              no_session_io):
    channel_id, conv = account
    with Session.begin() as db:
        db.get(Channel, channel_id).kind = db.get(Conversation, conv).channel = kind
    data = wav()
    path = f'/api/v1/inbox/conversations/{conv}/media'
    headers = {**tenant['headers'], 'Idempotency-Key': 'voice'}
    args = {'files': {'file': ('reply.wav', data, 'audio/x-wav')}}
    assert client.post(path, **args).status_code == 401
    response = client.post(path, headers=headers, **args)
    assert response.status_code == 202 and client.post(path, headers=headers, **args).json() == response.json()
    identity = response.json()['message_id']
    calls, interpretations = [], []
    monkeypatch.setattr('app.services.media_intelligence.transcribe', lambda *args: interpretations.append(args))
    def route(request):
        assert no_session_io() and request.url.path.endswith('/me/messages')
        assert data in request.content and b'filename="attachment.wav"' in request.content
        assert b'"type": "audio"' in request.content and b'"id": "987654"' in request.content
        calls.append(request)
        assert not meta_dm_replies.dispatch_one()
        return httpx.Response(200, json={'message_id': 'mid-1', 'recipient_id': '987654'})
    real_client = httpx.Client
    monkeypatch.setattr(meta_outbound_media.httpx, 'Client', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(route)))
    assert meta_dm_replies.dispatch_one() and not meta_dm_replies.dispatch_one()
    assert len(calls) == 1 and interpretations == []
    with Session.begin() as db:
        message = db.get(Message, identity)
        assert message.kind == 'audio' and message.upload_data is None and message.delivery_status == 'sent'
        assert message.attachments[0]['mime_type'] == 'audio/wav'
        assert not message.attachments[0].get('transcript')
        message.dispatch_started_at = now() - timedelta(seconds=3)
        message.meta_acknowledged_at = now() - timedelta(seconds=2)
        item = message.attachments[0]
    assert meta_receipts.store(receipt(kind, 'read', watermark=int(now().timestamp() * 1000))) == 1
    assert meta_receipts.process_one()
    with Session() as db:
        assert db.get(Message, identity).delivery_status == 'read'
    url = f'/api/v1/conversations/{conv}/media/{item["id"]}'
    downloaded = client.get(url, headers=tenant['headers'])
    assert downloaded.content == data and downloaded.headers['cache-control'] == 'private, no-store'
    assert downloaded.headers['content-disposition'].startswith('inline;')
    assert client.get(url).status_code == 401
    foreign = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                          json={'name': 'Foreign audio'}).json()
    assert client.get(url, headers={'X-Admin-Key': foreign['admin_key']}).status_code == 404
    assert client.post(path, headers={'X-Admin-Key': foreign['admin_key'], 'Idempotency-Key': 'foreign'},
                       **args).status_code == 404
    with Session.begin() as db:
        db.get(Message, identity).created_at = now() - timedelta(days=settings.media_retention_days + 1)
    media.expire()
    assert client.get(url, headers=tenant['headers']).status_code == 404
    assert not media.storage()._path(item['key']).exists()


def test_audio_permission_validation_and_whatsapp_boundary(client, tenant, account, monkeypatch):
    channel, conv = account
    path = f'/api/v1/inbox/conversations/{conv}/media'
    args = {'files': {'file': ('reply.wav', wav(), 'audio/wav')}}
    headers = {**tenant['headers'], 'Idempotency-Key': 'validation'}
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Reader', key_prefix='reader',
            key_hash=hashlib.sha256(b'audio-reader').hexdigest(), scopes=['viewer']))
    assert client.post(path, headers={'Authorization': 'Bearer audio-reader', 'Idempotency-Key': 'v'},
                       **args).status_code == 403
    assert client.post(path, headers=tenant['headers'], **args).status_code == 422
    assert client.post(path.replace(conv, 'invalid'), headers=headers, **args).status_code == 422
    assert client.post(path, headers=headers, files={'file': ('bad.wav', b'invalid', 'audio/wav')}).status_code == 422
    assert client.post(path, headers=headers,
                       files={'file': ('bad.ogg', b'OggSfixture', 'audio/ogg')}).status_code == 422
    assert client.post(path, headers=headers, data={'caption': 'unsupported'}, **args).status_code == 422
    monkeypatch.setattr(settings, 'media_max_bytes', 1024)
    assert client.post(path, headers=headers, **args).status_code == 413
    monkeypatch.setattr(settings, 'media_max_bytes', 16777216)
    with Session.begin() as db:
        db.get(Conversation, conv).channel = db.get(Channel, channel).kind = 'whatsapp'
    assert client.post(path, headers=headers, **args).status_code == 422


@pytest.mark.parametrize(('mime', 'data', 'suffix'), [('audio/aac', b'\xff\xf1fixture', '.aac'),
                                                   ('audio/mp4', b'\x00\x00\x00\x18ftypM4A fixture', '.m4a')])
def test_other_audio_formats_use_audio_payload(client, tenant, account, monkeypatch, mime, data, suffix):
    with Session() as db:
        channel = db.get(Channel, account[0])
    def route(request):
        assert data in request.content and b'"type": "audio"' in request.content
        assert ('filename="attachment' + suffix + '"').encode() in request.content
        return httpx.Response(200, json={'message_id': 'audio-mid', 'recipient_id': '987654'})
    real_client = httpx.Client
    monkeypatch.setattr(meta_outbound_media.httpx, 'Client', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(route)))
    assert meta_outbound_media.send(channel, '987654', {'kind': 'audio', 'mime_type': mime}, data) == 'audio-mid'


def test_unknown_audio_is_not_replayed_or_transcribed(client, tenant, account, monkeypatch):
    _, conv = account
    path = f'/api/v1/inbox/conversations/{conv}/media'
    headers = {**tenant['headers'], 'Idempotency-Key': 'unknown-audio'}
    args = {'files': {'file': ('reply.m4a', encoded('m4a'), 'audio/x-m4a')}}
    first = client.post(path, headers=headers, **args)
    assert first.status_code == 202
    calls = []
    def timeout(*args):
        calls.append(args)
        raise MetaSignupError('Synthetic timeout')
    monkeypatch.setattr(meta_outbound_media, 'send', timeout)
    assert meta_dm_replies.dispatch_one() and not meta_dm_replies.dispatch_one()
    assert client.post(path, headers=headers, **args).json()['status'] == 'unknown'
    assert not meta_dm_replies.dispatch_one() and len(calls) == 1
    with Session() as db:
        message = db.scalar(select(Message).where(Message.conversation_id == conv))
        assert message.upload_data is None and message.attachments[0]['mime_type'] == 'audio/mp4'
        assert 'transcript' not in json.dumps(message.attachments)


@pytest.mark.parametrize(('mime', 'body', 'valid'), [
    ('audio/aac', b'\xff\xf1fixture', False), ('audio/mp4', b'\0\0\0\x18ftypfixture', False),
    ('audio/mp4', 'mp4', False), ('video/mp4', 'mp4', False),
    ('video/mp4', 'm4a', True), ('audio/aac', 'aac', True)])
def test_only_decodable_audio_without_video_is_queued(client, tenant, account, mime, body, valid):
    data = encoded(body) if isinstance(body, str) else body
    path = f'/api/v1/inbox/conversations/{account[1]}/media'
    response = client.post(path, headers={**tenant['headers'], 'Idempotency-Key': 'decode'},
                           files={'file': ('recording.mp4', data, mime)})
    assert response.status_code == (202 if valid else 422), response.text
    with Session() as db:
        identity = db.scalar(select(Message.id).where(Message.conversation_id == account[1]))
        assert (identity is not None) == valid


def test_claim_rechecks_committed_inflight_reply_after_locking(client, tenant, account, monkeypatch):
    _, conv = account
    with Session.begin() as db:
        earlier = Message(conversation_id=conv, role='agent', direction='outbound', kind='text',
                          content='Earlier reply', delivery_status='failed')
        db.add(earlier)
        db.flush()
        earlier_id = earlier.id
    response = client.post(f'/api/v1/inbox/conversations/{conv}/media',
        headers={**tenant['headers'], 'Idempotency-Key': 'race'},
        files={'file': ('reply.wav', wav(), 'audio/wav')})
    assert response.status_code == 202
    calls = []
    monkeypatch.setattr(meta_outbound_media, 'send', lambda *args: calls.append(args) or 'unexpected-mid')
    class Sessions:
        def __call__(self):
            return Session()
        @contextmanager
        def begin(self):
            with Session.begin() as db:
                execute = db.execute
                def after_selection(statement, *args, **kwargs):
                    result = execute(statement, *args, **kwargs)
                    if [item['name'] for item in getattr(statement, 'column_descriptions', [])] == [
                            'Message', 'Conversation', 'Tenant']:
                        # Model a competing claim becoming visible after the SELECT snapshot.
                        with Session.begin() as other:
                            other.get(Message, earlier_id).delivery_status = 'sending'
                    return result
                db.execute = after_selection
                yield db
    monkeypatch.setattr(meta_dm_replies, 'Session', Sessions())
    assert not meta_dm_replies.dispatch_one()
    assert calls == []
    with Session() as db:
        assert db.get(Message, response.json()['message_id']).delivery_status == 'queued'


def test_early_audio_read_waits_for_ack_and_preserves_mid_evidence(client, tenant, account):
    _, conv = account
    result = client.post(f'/api/v1/inbox/conversations/{conv}/media',
        headers={**tenant['headers'], 'Idempotency-Key': 'early-read'},
        files={'file': ('reply.wav', wav(), 'audio/wav')})
    identity = result.json()['message_id']
    read_at, delivered_at = now() - timedelta(seconds=2), now() - timedelta(seconds=3)
    with Session.begin() as db:
        message = db.get(Message, identity)
        message.delivery_status, message.dispatch_started_at = 'sending', now() - timedelta(seconds=5)
    assert meta_receipts.store(receipt(status='read', watermark=int(read_at.timestamp() * 1000))) == 1
    assert meta_receipts.process_one()
    with Session.begin() as db:
        pending = db.scalar(select(MetaReceipt))
        assert pending.status == 'queued'
        message = db.get(Message, identity)
        message.delivery_status, message.channel_message_id, message.meta_acknowledged_at = 'sent', 'mid-1', now()
    assert meta_receipts.store(receipt(watermark=int(delivered_at.timestamp() * 1000))) == 1
    assert meta_receipts.process_one()
    with Session() as db:
        assert db.get(Message, identity).delivery_status == 'read'


def test_missing_audio_validator_is_a_setup_error(client, tenant, account, monkeypatch):
    from app.services import audio_validation
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('private executable path')
    monkeypatch.setattr(audio_validation.subprocess, 'run', unavailable)
    response = client.post(f'/api/v1/inbox/conversations/{account[1]}/media',
        headers={**tenant['headers'], 'Idempotency-Key': 'unavailable'},
        files={'file': ('reply.wav', wav(), 'audio/wav')})
    assert response.status_code == 503 and 'private executable path' not in response.text
