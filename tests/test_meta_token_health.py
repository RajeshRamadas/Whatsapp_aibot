"""Meta credential checks preserve tenant boundaries and credential revisions."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import Channel, Membership, Session, User, engine, now
from app.services import email
from app.services import meta_token_health as health
from app.services.crypto import encrypt
from app.services.meta_signup import MetaSignupError
from tests import test_meta_dm_connections as connections
from tests.test_workspaces import Recorder

admin = connections.admin


@pytest.mark.parametrize('kind', ['instagram', 'messenger'])
def test_token_check_warns_before_expiry_and_keeps_only_metadata(client, admin, monkeypatch, kind):
    recorder = Recorder()
    monkeypatch.setattr(email, 'sender', recorder)
    tenant_id = admin['X-Workspace-Id']
    with Session.begin() as db:
        user = db.scalar(select(User).join(Membership).where(Membership.tenant_id == tenant_id))
        user.email_verified_at = now()
        channel = Channel(tenant_id=tenant_id, kind=kind, external_id='1234',
                          credentials_enc=encrypt(tenant_id, 'page-private'))
        db.add(channel)
        db.flush()
        channel_id = channel.id

    graph = connections.FakeGraph()
    expiry = int((now() + timedelta(hours=1)).timestamp())
    graph.debug.update(expires_at=0, data_access_expires_at=expiry)
    original = graph.request

    def request(path, **kwargs):
        assert engine.pool.checkedout() == 0
        return original(path, **kwargs)

    monkeypatch.setattr(graph, 'request', request)
    monkeypatch.setattr(health, 'MetaSignup', lambda: graph)
    assert health.process_one() and not health.process_one()
    assert graph.calls == [('debug_token', {'input_token': 'page-private'}, '123|test-app-secret', None, None)]
    assert len(recorder.sent) == 1 and recorder.sent[0][1] == 'Meta messaging account needs attention'
    assert 'page-private' not in str(recorder.sent)
    with Session.begin() as db:
        channel = db.get(Channel, channel_id)
        assert channel.meta_token_status == 'expiring' and channel.meta_send_ready
        assert int(channel.meta_token_check_at.timestamp()) == expiry
        assert channel.meta_token_expires_at == channel.meta_token_check_at and channel.meta_token_alerted
        channel.meta_token_check_at = now()
    graph.debug = {'is_valid': False}
    assert health.process_one()
    assert len(recorder.sent) == 1
    result = client.get('/api/v1/channels', headers=admin)
    assert 'page-private' not in result.text and result.json()[0]['meta_token_status'] == 'invalid'
    assert client.put(f'/api/v1/meta-dm/channels/{channel_id}/bot', headers=admin,
                      json={'enabled': True}).status_code == 409
    with Session() as db:
        assert not db.get(Channel, channel_id).meta_send_ready


@pytest.mark.parametrize('outcome', ['healthy', 'transient', 'malformed', 'replaced'])
def test_health_check_preserves_a_valid_or_replaced_credential(client, admin, monkeypatch, outcome):
    tenant_id = admin['X-Workspace-Id']
    with Session.begin() as db:
        channel = Channel(tenant_id=tenant_id, kind='messenger', external_id='1234',
                          credentials_enc=encrypt(tenant_id, 'previous-private'))
        db.add(channel)
        db.flush()
        channel_id = channel.id

    def request(*args, **kwargs):
        assert engine.pool.checkedout() == 0
        if outcome == 'transient':
            raise MetaSignupError('Temporary failure')
        if outcome == 'malformed':
            return {'data': {'is_valid': True, 'app_id': '123', 'expires_at': 'unknown'}}
        if outcome == 'replaced':
            with Session.begin() as db:
                channel = db.get(Channel, channel_id)
                channel.credentials_enc = encrypt(tenant_id, 'replacement-private')
                channel.meta_token_check_at = None
            return {'data': {'is_valid': False}}
        return {'data': {'is_valid': True, 'app_id': '123', 'expires_at': 0}}

    graph = connections.FakeGraph()
    monkeypatch.setattr(graph, 'request', request)
    monkeypatch.setattr(health, 'MetaSignup', lambda: graph)
    assert health.process_one()
    with Session() as db:
        channel = db.get(Channel, channel_id)
        assert channel.meta_send_ready
        if outcome == 'healthy':
            assert channel.meta_token_status == 'valid'
            assert channel.meta_token_check_at > now() + timedelta(hours=23)
        elif outcome in ('transient', 'malformed'):
            assert channel.meta_token_status is None
            assert now() < channel.meta_token_check_at <= now() + health.RETRY_INTERVAL
        else:
            assert channel.meta_token_status is None and channel.meta_token_check_at is None


def test_unreadable_credential_pauses_even_when_email_staging_fails(client, admin, monkeypatch):
    tenant_id = admin['X-Workspace-Id']
    encrypted = encrypt(tenant_id, 'page-private')
    with Session.begin() as db:
        user = db.scalar(select(User).join(Membership).where(Membership.tenant_id == tenant_id))
        user.email_verified_at = now()
        channel = Channel(tenant_id=tenant_id, kind='messenger', external_id='1234',
                          credentials_enc=encrypted[:-1] + bytes([encrypted[-1] ^ 1]), meta_token_status='valid')
        db.add(channel)
        db.flush()
        channel_id = channel.id
    graph = connections.FakeGraph()
    monkeypatch.setattr(health, 'MetaSignup', lambda: graph)

    def fail_email(*args):
        raise RuntimeError('Email unavailable')

    with monkeypatch.context() as context:
        context.setattr(email.sender, 'stage', fail_email)
        assert health.process_one()
    with Session.begin() as db:
        channel = db.get(Channel, channel_id)
        assert channel.meta_token_status == 'invalid' and not channel.meta_send_ready
        assert not channel.meta_token_alerted and channel.meta_token_check_at <= now() + health.RETRY_INTERVAL
        channel.meta_token_check_at = now()
    recorder = Recorder()
    monkeypatch.setattr(email, 'sender', recorder)
    assert health.process_one() and not health.process_one()
    assert len(recorder.sent) == 1 and not graph.calls
    with Session() as db:
        assert db.get(Channel, channel_id).meta_token_alerted
