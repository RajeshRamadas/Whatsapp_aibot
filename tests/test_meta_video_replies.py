"""Real MP4 validation plus private, ordered and single-attempt Meta video delivery."""
import hashlib
import subprocess
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db import ApiKey, Channel, Conversation, Message, Session, now
from app.services import audio_validation, media, meta_dm_replies, meta_outbound_media, meta_receipts
from app.services.meta_signup import MetaSignupError
from tests import test_meta_outbound_media
from tests.test_meta_receipts import data as receipt

account = test_meta_outbound_media.account
no_session_io = test_meta_outbound_media.no_session_io


@lru_cache
def clip(codec='libx264', audio=None, size='320x180'):
    with TemporaryDirectory() as directory:
        path = Path(directory) / 'clip.mp4'
        args = ['-f', 'lavfi', '-i', 'color=c=blue:s=' + size + ':r=10']
        if audio:
            args += ['-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono', '-c:a', audio]
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *args, '-threads', '1',
                        '-c:v', codec, '-pix_fmt', 'yuv420p', '-t', '1', '-y', str(path)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
        return path.read_bytes()


def queue(client, tenant, conv, payload=None, key='video', **kwargs):
    return client.post(f'/api/v1/inbox/conversations/{conv}/media',
        headers={**tenant['headers'], 'Idempotency-Key': key},
        files={'file': ('reply.mp4', clip() if payload is None else payload, 'video/mp4')}, **kwargs)


@pytest.mark.parametrize('audio', [None, 'aac'])
def test_native_video_decoder_accepts_real_clip(monkeypatch, audio):
    data, run = clip(audio=audio), audio_validation.subprocess.run
    def diagnostic(command, **kwargs):
        kwargs['stderr'] = subprocess.PIPE
        try:
            return run(command, **kwargs)
        except subprocess.CalledProcessError as exc:
            # Synthetic fixture only: surface native-tool startup failures in CI.
            pytest.fail('Native media decoder failed: ' + exc.stderr.decode(errors='replace')[-1500:])
    monkeypatch.setattr(audio_validation.subprocess, 'run', diagnostic)
    assert audio_validation.validate(data, 'video/mp4') == 'video'


@pytest.mark.parametrize('kind', ['instagram', 'messenger'])
@pytest.mark.parametrize('audio', [None, 'aac'])
def test_video_delivery_private_playback_receipts_and_expiry(client, tenant, account, monkeypatch,
                                                           no_session_io, kind, audio):
    channel, conv = account
    with Session.begin() as db:
        db.get(Conversation, conv).channel = db.get(Channel, channel).kind = kind
    data = clip(audio=audio)
    result = queue(client, tenant, conv, data)
    assert result.status_code == 202 and queue(client, tenant, conv, data).json() == result.json()
    assert queue(client, tenant, conv, data, data={'caption': 'Changed'}).status_code == 409
    identity, calls = result.json()['message_id'], []
    real_client = httpx.Client
    def route(request):
        assert no_session_io()
        assert data in request.content and b'filename="attachment.mp4"' in request.content
        assert b'"type": "video"' in request.content and b'"id": "987654"' in request.content
        calls.append(request)
        assert not meta_dm_replies.dispatch_one()
        return httpx.Response(200, json={'message_id': 'mid-1', 'recipient_id': '987654'})
    monkeypatch.setattr(meta_outbound_media.httpx, 'Client', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(route)))
    assert meta_dm_replies.dispatch_one() and not meta_dm_replies.dispatch_one()
    assert len(calls) == 1
    with Session.begin() as db:
        message = db.get(Message, identity)
        assert message.kind == 'video' and message.delivery_status == 'sent' and message.upload_data is None
        assert message.attachments[0]['mime_type'] == 'video/mp4'
        message.dispatch_started_at = now() - timedelta(seconds=3)
        message.meta_acknowledged_at = now() - timedelta(seconds=2)
        item = message.attachments[0]
    assert meta_receipts.store(receipt(kind, 'read', watermark=int(now().timestamp() * 1000))) == 1
    assert meta_receipts.process_one()
    with Session() as db:
        assert db.get(Message, identity).delivery_status == 'read'
    url = f'/api/v1/conversations/{conv}/media/{item["id"]}'
    response = client.get(url, headers=tenant['headers'])
    assert response.content == data and response.headers['content-type'] == 'video/mp4'
    assert response.headers['content-disposition'].startswith('inline;')
    assert response.headers['cache-control'] == 'private, no-store'
    partial = client.get(url, headers={**tenant['headers'], 'Range': 'bytes=0-15'})
    assert partial.status_code == 206 and partial.content == data[:16]
    with Session.begin() as db:
        db.get(Message, identity).created_at = now() - timedelta(days=settings.media_retention_days + 1)
    media.expire()
    assert client.get(url, headers=tenant['headers']).status_code == 404
    assert not media.storage()._path(item['key']).exists()


def test_video_auth_isolation_validation_and_whatsapp_boundary(client, tenant, account, monkeypatch):
    channel, conv = account
    path = f'/api/v1/inbox/conversations/{conv}/media'
    args = {'files': {'file': ('reply.mp4', clip(), 'video/mp4')}}
    assert client.post(path, **args).status_code == 401
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Reader', key_prefix='reader',
            key_hash=hashlib.sha256(b'video-reader').hexdigest(), scopes=['viewer']))
    assert client.post(path, headers={'Authorization': 'Bearer video-reader', 'Idempotency-Key': 'v'},
                       **args).status_code == 403
    assert client.post(path, headers=tenant['headers'], **args).status_code == 422
    assert client.post(path.replace(conv, 'invalid'), headers=tenant['headers'], **args).status_code == 422
    foreign = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                          json={'name': 'Other video workspace'}).json()
    other = {'headers': {'X-Admin-Key': foreign['admin_key']}}
    assert queue(client, other, conv).status_code == 404
    result = queue(client, tenant, conv)
    with Session() as db:
        item = db.get(Message, result.json()['message_id']).attachments[0]
    url = f'/api/v1/conversations/{conv}/media/{item["id"]}'
    assert client.get(url).status_code == 401
    assert client.get(url, headers=other['headers']).status_code == 404
    assert queue(client, tenant, conv, key='caption', data={'caption': 'Unsupported'}).status_code == 422
    monkeypatch.setattr(settings, 'media_max_bytes', 1024)
    assert queue(client, tenant, conv, key='oversize').status_code == 413
    monkeypatch.setattr(settings, 'media_max_bytes', 16777216)
    with Session.begin() as db:
        db.get(Conversation, conv).channel = db.get(Channel, channel).kind = 'whatsapp'
    assert queue(client, tenant, conv, key='whatsapp').status_code == 422


@pytest.mark.parametrize('case', ['fake', 'codec', 'audio', 'dimensions'])
def test_invalid_video_never_queues(client, tenant, account, case):
    data = {'fake': lambda: b'\0\0\0\x18ftypfixture', 'codec': lambda: clip('mpeg4'),
            'audio': lambda: clip(audio='libmp3lame'), 'dimensions': lambda: clip(size='1922x16')}[case]()
    assert queue(client, tenant, account[1], data).status_code == 422
    with Session() as db:
        assert db.scalar(select(Message.id).where(Message.conversation_id == account[1])) is None


def test_decoder_failure_and_missing_tools_do_not_queue(client, tenant, account, monkeypatch):
    run = audio_validation.subprocess.run
    def broken(command, **kwargs):
        if 'ffmpeg' in command:
            raise subprocess.TimeoutExpired('private file path', 5)
        return run(command, **kwargs)
    data = clip()
    monkeypatch.setattr(audio_validation.subprocess, 'run', broken)
    assert queue(client, tenant, account[1], data).status_code == 422
    def missing_decoder(command, **kwargs):
        if command[0] == 'prlimit':
            raise subprocess.CalledProcessError(127, command)
        return run(command, **kwargs)
    monkeypatch.setattr(audio_validation.subprocess, 'run', missing_decoder)
    assert queue(client, tenant, account[1], data).status_code == 503
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('private executable path')
    monkeypatch.setattr(audio_validation.subprocess, 'run', unavailable)
    result = queue(client, tenant, account[1], data)
    assert result.status_code == 503 and 'private executable path' not in result.text
    with Session() as db:
        assert db.scalar(select(Message.id).where(Message.conversation_id == account[1])) is None


def test_video_with_unplayable_optional_audio_is_rejected(monkeypatch):
    data, run = clip(audio='aac'), audio_validation.subprocess.run
    def empty_audio(command, **kwargs):
        if '0:a:0' in command:
            return subprocess.CompletedProcess(command, 0, stdout=b'')
        return run(command, **kwargs)
    monkeypatch.setattr(audio_validation.subprocess, 'run', empty_audio)
    with pytest.raises(ValueError):
        audio_validation.validate(data, 'video/mp4')


@pytest.mark.parametrize('reason', ['unknown', 'window', 'disconnect'])
def test_video_staging_guards_and_unknown_delivery_never_replay(client, tenant, account, monkeypatch, reason):
    channel, conv = account
    result = queue(client, tenant, conv)
    assert result.status_code == 202
    calls, stage = [], meta_outbound_media.stage
    def staged(*args):
        stage(*args)
        with Session.begin() as db:
            if reason == 'window':
                db.get(Conversation, conv).last_inbound_at = now() - timedelta(hours=25)
            elif reason == 'disconnect':
                db.get(Channel, channel).credentials_enc = None
    def uncertain(*args):
        calls.append(args)
        raise MetaSignupError('Synthetic uncertain video delivery')
    monkeypatch.setattr(meta_outbound_media, 'stage', staged)
    monkeypatch.setattr(meta_outbound_media, 'send', uncertain)
    assert meta_dm_replies.dispatch_one() and not meta_dm_replies.dispatch_one()
    assert len(calls) == (1 if reason == 'unknown' else 0)
    replay = queue(client, tenant, conv)
    assert replay.json()['status'] == ('unknown' if reason == 'unknown' else 'failed')
    assert not meta_dm_replies.dispatch_one()
    with Session() as db:
        assert db.get(Message, result.json()['message_id']).upload_data is None
