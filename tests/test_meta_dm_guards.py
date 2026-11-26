"""Regression checks for delayed events, channel replacement and concurrent sends."""
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api.meta_dm import store
from app.config import settings
from app.db import Channel, Conversation, Message, MetaDmEvent, Session, Tenant, engine, now
from app.services import meta_dm
from app.services import meta_dm_replies as replies
from app.services.crypto import encrypt
from tests import test_meta_dm_connections as connections
from tests.test_meta_dm import payload
from tests.test_meta_dm_replies import setup_conversation

admin = connections.admin


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(settings, 'master_key', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    for target in ('app.services.meta_dm.publish_sync', 'app.services.meta_dm_replies.publish_sync',
                   'app.api.inbox.publish_sync', 'app.api.meta_dm_connections.publish_sync'):
        monkeypatch.setattr(target, lambda *args: None)


def queued(client, tenant, kind='messenger'):
    channel_id, conv_id = setup_conversation(tenant['tenant_id'], channel=kind)
    with Session.begin() as db:
        db.get(Channel, channel_id).credentials_enc = encrypt(tenant['tenant_id'], 'fake-token')
    path = '/api/v1/inbox/conversations/' + conv_id + '/reply'
    result = client.post(path, headers=tenant['headers'], json={'message': 'First'})
    assert result.status_code == 200
    return channel_id, conv_id, result.json()['message_id'], path


@pytest.mark.parametrize('stamp', [None, True, -1, 10**30, 'yesterday'])
def test_invalid_provider_time_never_opens_window(client, tenant, stamp):
    with Session.begin() as db:
        db.add(Channel(tenant_id=tenant['tenant_id'], kind='messenger', external_id='page-123'))
    data = payload()
    data['entry'][0]['messaging'][0]['timestamp'] = stamp
    assert store(data) == 1 and meta_dm.process_one()
    with Session() as db:
        conv = db.scalar(select(Conversation).where(Conversation.tenant_id == tenant['tenant_id']))
        assert conv.last_inbound_at is None


def test_delayed_and_out_of_order_events_preserve_provider_window(client, tenant):
    with Session.begin() as db:
        db.add(Channel(tenant_id=tenant['tenant_id'], kind='messenger', external_id='page-123'))
    for index, hours in enumerate((25, 1, 2)):
        data = payload(mid='m' + str(index))
        stamp = now() - timedelta(hours=hours)
        data['entry'][0]['messaging'][0]['timestamp'] = int(stamp.timestamp() * 1000)
        assert store(data) == 1 and meta_dm.process_one()
        with Session() as db:
            conv = db.scalar(select(Conversation).where(Conversation.tenant_id == tenant['tenant_id']))
            age = now() - conv.last_inbound_at
            assert timedelta(hours=24 if index == 0 else 0) < age
            if index:
                assert timedelta(hours=1) <= age < timedelta(hours=1, minutes=1)


@pytest.mark.parametrize(('kind', 'limit'), [('instagram', 1000), ('messenger', 2000)])
def test_inbox_reports_meta_eligibility_and_rejects_oversized_text(client, tenant, kind, limit):
    channel_id, conv_id, mid, path = queued(client, tenant, kind)
    view = client.get('/api/v1/inbox/conversations', headers=tenant['headers']).json()['items'][0]
    assert view['can_reply_freely']
    assert client.post(path, headers=tenant['headers'], json={'message': 'x' * (limit + 1)}).status_code == 422
    assert client.post(path, headers=tenant['headers'], json={'message': 'ह' * (limit // 3 + 1)}).status_code == 422
    assert client.patch('/api/v1/channels/' + channel_id, headers=tenant['headers'],
                        json={'access_token': 'unverified'}).status_code == 409


def test_replies_do_not_hold_sessions_or_overtake_inflight_conversation(client, tenant, monkeypatch):
    _, conv_id, first, path = queued(client, tenant)
    second = client.post(path, headers=tenant['headers'], json={'message': 'Second'}).json()['message_id']
    calls = []
    def send(channel, recipient, content):
        assert engine.pool.checkedout() == 0
        calls.append(content)
        assert not replies.dispatch_one()
        return 'sent-' + content
    monkeypatch.setattr(replies, 'send', send)
    assert replies.dispatch_one() and replies.dispatch_one() and not replies.dispatch_one()
    assert calls == ['First', 'Second']
    with Session() as db:
        assert all(db.get(Message, mid).delivery_status == 'sent' for mid in (first, second))


@pytest.mark.parametrize('change', ['closed', 'suspended', 'disconnected'])
def test_worker_revalidates_after_claim_before_http(client, tenant, monkeypatch, change):
    channel_id, conv_id, mid, _ = queued(client, tenant)
    first = True
    class Sessions:
        def __call__(self):
            return Session()
        @contextmanager
        def begin(self):
            nonlocal first
            with Session.begin() as db:
                yield db
            if first:
                first = False
                with Session.begin() as db:
                    if change == 'closed':
                        db.get(Conversation, conv_id).status = 'closed'
                    elif change == 'suspended':
                        db.get(Tenant, tenant['tenant_id']).suspended = True
                    else:
                        db.delete(db.get(Channel, channel_id))
    monkeypatch.setattr(replies, 'Session', Sessions())
    monkeypatch.setattr(replies, 'send', lambda *a: pytest.fail('Ineligible send'))
    assert replies.dispatch_one()
    with Session() as db:
        assert db.get(Message, mid).delivery_status == 'failed'


def test_unknown_delivery_never_retries_and_stale_status_is_published(client, tenant, monkeypatch):
    _, conv_id, mid, _ = queued(client, tenant)
    def unavailable(*args):
        raise RuntimeError('private provider detail')
    monkeypatch.setattr(replies, 'send', unavailable)
    assert replies.dispatch_one() and not replies.dispatch_one()
    with Session.begin() as db:
        message = db.get(Message, mid)
        assert message.delivery_status == 'unknown' and 'private' not in message.error
        message.delivery_status = 'sending'
        message.dispatch_started_at = now() - timedelta(minutes=3)
    events = []
    monkeypatch.setattr(replies, 'publish_sync', lambda *args: events.append(args))
    replies.expire_stale()
    assert events == [(tenant['tenant_id'], 'message.updated', {'conversation_id': conv_id})]


def test_disconnect_cancels_queue_and_replacement_cannot_inherit_history(client, admin, monkeypatch):
    monkeypatch.setattr('app.api.meta_dm_connections.MetaSignup', connections.FakeGraph)
    tenant_id = admin['X-Workspace-Id']
    body = {'kind': 'messenger', 'external_id': '12345', 'access_token': 'fake-token'}
    channel_id = client.post('/api/v1/meta-dm/channels', headers=admin, json=body).json()['id']
    data = payload(recipient='12345')
    data['entry'][0]['messaging'][0]['timestamp'] = int(now().timestamp() * 1000)
    assert store(data) == 1 and meta_dm.process_one()
    with Session() as db:
        original = db.scalar(select(Conversation.id).where(Conversation.tenant_id == tenant_id))
    reply = client.post('/api/v1/inbox/conversations/' + original + '/reply', headers=admin,
                        json={'message': 'Old asset reply'}).json()
    assert store(payload(mid='pending', recipient='12345')) == 1
    assert client.delete('/api/v1/meta-dm/channels/' + channel_id, headers=admin).status_code == 204
    replacement = client.post('/api/v1/meta-dm/channels', headers=admin, json=body).json()['id']
    assert replacement != channel_id
    assert not replies.dispatch_one()
    assert store(payload(mid='new-asset', recipient='12345')) == 1 and meta_dm.process_one()
    with Session() as db:
        assert db.get(Message, reply['message_id']).delivery_status == 'failed'
        assert db.get(Conversation, original).meta_channel_id is None
        assert len(db.scalars(select(Conversation).where(Conversation.tenant_id == tenant_id)).all()) == 2
        fresh = db.scalar(select(Conversation).where(Conversation.meta_channel_id == replacement))
        assert fresh.contact_id is None
        event = db.scalar(select(MetaDmEvent).where(MetaDmEvent.provider_message_id == 'messenger:pending'))
        assert event.status == 'ignored'
