"""Push opt-in isolation and delivery use local fakes, never external push services."""
from datetime import timedelta

import pytest
from pywebpush import WebPushException
from requests import Response
from sqlalchemy import select

from app.config import settings
from app.db import Conversation, InboxPushSubscription, Membership, Session, now
from app.services import inbox_push
from tests.test_workspaces import Recorder, login

ENDPOINT = 'https://fcm.googleapis.com/fcm/send/test-device'
BODY = {'endpoint': ENDPOINT, 'keys': {'p256dh': 'B' + 'A' * 86, 'auth': 'A' * 22}}
URL = '/api/v1/inbox/push-subscription'


@pytest.fixture
def agent(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    monkeypatch.setattr(settings, 'inbox_vapid_public_key', 'test-public')
    monkeypatch.setattr(settings, 'inbox_vapid_private_key', 'test-private')
    monkeypatch.setattr(settings, 'inbox_vapid_subject', 'mailto:test@example.com')
    headers = login(client, 'push@example.com', Recorder())
    response = client.post('/api/v1/workspaces', json={'name': 'Push workspace'}, headers=headers)
    assert response.status_code == 201
    headers['X-Workspace-Id'] = response.json()['tenant_id']
    user = client.get('/api/v1/auth/me').json()['id']
    return headers, user


def status(client, headers):
    return client.get(URL, headers=headers, params={'endpoint': ENDPOINT})


def test_opt_in_is_idempotent_and_workspace_scoped(client, agent):
    headers, _ = agent
    assert status(client, headers).json() == {'enabled': False}
    for _ in range(2):
        assert client.put(URL, headers=headers, json=BODY).status_code == 204
    with Session() as db:
        assert len(db.scalars(select(InboxPushSubscription)).all()) == 1
    assert status(client, headers).json() == {'enabled': True}
    second = client.post('/api/v1/workspaces', headers=headers, json={'name': 'Second workspace'}).json()['tenant_id']
    other = {**headers, 'X-Workspace-Id': second}
    assert status(client, other).json() == {'enabled': False}
    assert client.request('DELETE', URL, headers=other, json={'endpoint': ENDPOINT}).status_code == 204
    assert status(client, headers).json() == {'enabled': True}
    assert client.request('DELETE', URL, headers=headers, json={'endpoint': ENDPOINT}).status_code == 204
    assert status(client, headers).json() == {'enabled': False}


def test_other_member_cannot_remove_subscription(client, agent):
    headers, _ = agent
    assert client.put(URL, headers=headers, json=BODY).status_code == 204
    client.cookies.clear()
    other = login(client, 'other-push@example.com', Recorder())
    user_id = client.get('/api/v1/auth/me').json()['id']
    with Session.begin() as db:
        db.add(Membership(user_id=user_id, tenant_id=headers['X-Workspace-Id'], role='agent'))
    other['X-Workspace-Id'] = headers['X-Workspace-Id']
    assert status(client, other).json() == {'enabled': False}
    assert client.request('DELETE', URL, headers=other, json={'endpoint': ENDPOINT}).status_code == 204
    with Session() as db:
        assert len(db.scalars(select(InboxPushSubscription)).all()) == 1


@pytest.mark.parametrize('method,path,body', [
    ('GET', '/api/v1/inbox/push-config', None),
    ('GET', URL + '?endpoint=' + ENDPOINT, None),
    ('PUT', URL, BODY),
    ('DELETE', URL, {'endpoint': ENDPOINT}),
])
def test_auth_role_and_tenant_boundaries(client, agent, method, path, body):
    headers, user_id = agent
    assert client.request(method, path, headers={**headers, 'X-Workspace-Id': 'missing'},
                          json=body).status_code == 404
    with Session.begin() as db:
        member = db.scalar(select(Membership).where(Membership.user_id == user_id))
        member.role = 'viewer'
    assert client.request(method, path, headers=headers, json=body).status_code == 403
    client.cookies.clear()
    assert client.request(method, path, json=body).status_code == 401


@pytest.mark.parametrize('endpoint', ['http://fcm.googleapis.com/send', 'https://127.0.0.1/send',
                                     'https://fcm.googleapis.com.evil.example/send',
                                     'https://fcm.googleapis.com:444/send'])
def test_rejects_unsupported_endpoint(client, agent, endpoint):
    headers, _ = agent
    assert client.put(URL, headers=headers, json={**BODY, 'endpoint': endpoint}).status_code == 422


def test_configuration_validation_and_csrf(client, agent, tenant, monkeypatch):
    headers, _ = agent
    assert client.put(URL, headers={'X-Workspace-Id': headers['X-Workspace-Id']}, json=BODY).status_code == 403
    assert client.put(URL, headers=headers, json={**BODY, 'keys': {'p256dh': 'bad', 'auth': 'bad'}}).status_code == 422
    assert client.get(URL, headers=headers).status_code == 422
    assert client.put(URL, headers=tenant['headers'], json=BODY).status_code == 403
    monkeypatch.setattr(settings, 'inbox_vapid_private_key', None)
    assert client.get('/api/v1/inbox/push-config', headers=headers).json() == {'public_key': None}
    assert client.put(URL, headers=headers, json=BODY).status_code == 503


def due():
    with Session.begin() as db:
        for subscription in db.scalars(select(InboxPushSubscription)):
            subscription.next_attempt_at = now() - timedelta(seconds=1)


def waiting(client, agent):
    headers, _ = agent
    assert client.put(URL, headers=headers, json=BODY).status_code == 204
    with Session.begin() as db:
        conversation = Conversation(tenant_id=headers['X-Workspace-Id'], channel='whatsapp',
                                    external_user='fixture', status='waiting',
                                    last_message_at=now() - timedelta(hours=1))
        db.add(conversation)
        db.flush()
        return conversation.id


def test_delivery_survives_old_messages_and_deduplicates(client, agent, monkeypatch):
    conversation_id = waiting(client, agent)
    sent = []
    monkeypatch.setattr(inbox_push, 'webpush', lambda **kwargs: sent.append(kwargs))
    inbox_push.deliver_waiting()
    assert len(sent) == 1
    assert 'fixture' not in sent[0]['data']
    due()
    inbox_push.deliver_waiting()
    assert len(sent) == 1
    with Session.begin() as db:
        db.get(Conversation, conversation_id).last_message_at = now()
    due()
    inbox_push.deliver_waiting()
    assert len(sent) == 2


def test_transient_failure_retries_and_expired_subscription_removed(client, agent, monkeypatch):
    waiting(client, agent)
    response = Response()
    response.status_code = 503

    def fail(**kwargs):
        raise WebPushException('test', response=response)

    monkeypatch.setattr(inbox_push, 'webpush', fail)
    inbox_push.deliver_waiting()
    with Session() as db:
        subscription = db.scalar(select(InboxPushSubscription))
        assert subscription.notification_state == {}
    due()
    response.status_code = 410
    inbox_push.deliver_waiting()
    with Session() as db:
        assert db.scalar(select(InboxPushSubscription)) is None


def test_demoted_members_and_claimed_subscriptions_are_skipped(client, agent, monkeypatch):
    waiting(client, agent)
    sent = []
    monkeypatch.setattr(inbox_push, 'webpush', lambda **kwargs: sent.append(kwargs))
    with Session.begin() as db:
        subscription = db.scalar(select(InboxPushSubscription))
        subscription.next_attempt_at = now() + timedelta(minutes=2)
    inbox_push.deliver_waiting()
    assert sent == []
    due()
    with Session.begin() as db:
        db.scalar(select(Membership).where(Membership.user_id == agent[1])).role = 'viewer'
    inbox_push.deliver_waiting()
    assert sent == []


def test_push_migrations_upgrade_and_downgrade(client):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from app.db import engine

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'migrations'))
    command.downgrade(config, '0012_message_sources')
    assert 'inbox_push_subscriptions' not in inspect(engine).get_table_names()
    command.upgrade(config, 'head')
    columns = {column['name'] for column in inspect(engine).get_columns('inbox_push_subscriptions')}
    assert {'notification_state', 'next_attempt_at', 'endpoint_hash', 'tenant_id', 'user_id'} <= columns
