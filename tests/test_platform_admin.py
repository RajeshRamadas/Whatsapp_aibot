from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import rag
from app.config import settings
from app.db import (
    AuditLog,
    Conversation,
    InboundEvent,
    Membership,
    Session,
    Source,
    Tenant,
    UsageEvent,
    User,
    WidgetTurn,
    now,
    uid,
)
from app.services import email, usage
from app.services.widget_turns import process_turn
from app.whatsapp import handle
from tests.test_workspaces import Recorder, login

BASE = '/api/v1/platform'


@pytest.fixture
def admin(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    monkeypatch.setattr(email, 'sender', Recorder())
    headers = login(client, 'platform@example.com', email.sender)
    with Session.begin() as db:
        db.scalar(select(User).where(User.email == 'platform@example.com')).is_platform_admin = True
    return headers


@pytest.mark.parametrize('path', ['/me', '/tenants', '/failed-jobs', '/webhook-failures'])
def test_platform_reads_require_platform_session(client, tenant, path, monkeypatch):
    assert client.get(BASE + path).status_code == 401
    assert client.get(BASE + path, headers=tenant['headers']).status_code == 401
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    monkeypatch.setattr(email, 'sender', Recorder())
    headers = login(client, 'owner@example.com', email.sender)
    assert client.post('/api/v1/workspaces', headers=headers, json={'name': 'Owned workspace'}).status_code == 201
    assert client.get(BASE + path, headers={'X-Workspace-Id': tenant['tenant_id']}).status_code == 403


def test_tenant_totals_are_live_paginated_and_secret_free(client, tenant, admin):
    with Session.begin() as db:
        db.add_all([
            UsageEvent(tenant_id=tenant['tenant_id'], kind='answer'),
            UsageEvent(tenant_id=tenant['tenant_id'], kind='chat', input_tokens=100, output_tokens=20,
                       cost_usd=Decimal('0.3')),
            UsageEvent(tenant_id=tenant['tenant_id'], kind='embedding'),
            UsageEvent(tenant_id=tenant['tenant_id'], kind='answer',
                       created_at=usage.month_start(now()) - timedelta(seconds=1)),
        ])
    response = client.get(BASE + '/tenants?limit=1')
    assert response.status_code == 200, response.text
    row = response.json()['items'][0]
    assert (row['id'], row['plan'], row['ai_messages']) == (tenant['tenant_id'], 'starter', 1)
    assert (row['input_tokens'], row['output_tokens'], row['unpriced_calls']) == (100, 20, 1)
    assert Decimal(row['cost_usd']) == Decimal('0.3')
    assert 'key' not in response.text and 'billing_details' not in response.text
    assert client.get(BASE + '/tenants?offset=1').json()['items'] == []
    assert client.get(BASE + '/tenants?search=_').json()['total'] == 0
    assert client.get(BASE + '/tenants?search=Demo').json()['total'] == 1
    for suffix in ('tenants?limit=0', 'failed-jobs?offset=-1', 'webhook-failures?limit=101'):
        assert client.get(BASE + '/' + suffix).status_code == 422


def test_failure_lists_omit_customer_data_and_record_webhook_rejections(client, tenant, admin):
    with Session.begin() as db:
        conversation = Conversation(id=uid(), tenant_id=tenant['tenant_id'], channel='web')
        db.add(conversation)
        db.flush()
        db.add_all([
            Source(tenant_id=tenant['tenant_id'], filename='private-name.pdf', kind='pdf', file_path='secret',
                   status='failed', error='secret phone 15551234567'),
            InboundEvent(tenant_id=tenant['tenant_id'], channel_message_id='secret-provider-id', status='failed',
                         payload={'text': 'private text', 'from': '15551234567'}, last_error='secret'),
            WidgetTurn(id=uid(), conversation_id=conversation.id, message='private text', state='failed'),
            InboundEvent(tenant_id=tenant['tenant_id'], channel_message_id='queued', status='queued'),
        ])
    response = client.get(BASE + '/failed-jobs')
    assert response.status_code == 200
    assert {row['kind'] for row in response.json()['items']} == {'ingestion', 'whatsapp', 'widget'}
    assert response.json()['total'] == 3
    assert client.get(BASE + '/failed-jobs?limit=2&offset=2').json()['items'] == response.json()['items'][2:]
    for private in ('private', 'secret', '15551234567', 'payload', 'last_error'):
        assert private not in response.text
    assert client.post('/webhooks/whatsapp', content='private text').status_code == 401
    failures = client.get(BASE + '/webhook-failures').json()
    assert failures['total'] == 1
    assert failures['items'][0]['provider'] == 'whatsapp'
    assert failures['items'][0]['http_status'] == 401
    assert 'private' not in str(failures)


def test_suspend_resume_auth_keys_and_bot_processing(client, tenant, admin, chats, sent):
    admin_cookies = dict(client.cookies)
    user_headers = login(client, 'member@example.com', email.sender)
    user_cookies = dict(client.cookies)
    with Session.begin() as db:
        user = db.scalar(select(User).where(User.email == 'member@example.com'))
        db.add(Membership(user_id=user.id, tenant_id=tenant['tenant_id'], role='owner'))
    base = '/api/v1/chat/' + tenant['public_key']
    turn = client.post(base + '/widget/turns', json={'request_id': uid(), 'message': 'Hello'}).json()
    path = BASE + '/tenants/' + tenant['tenant_id'] + '/suspension'
    assert client.put(path, headers=user_headers, json={'suspended': True}).status_code == 403
    client.cookies.clear()
    assert client.put(path, json={'suspended': True}).status_code == 401
    client.cookies.update(admin_cookies)
    assert client.put(path, json={'suspended': True}).status_code == 403
    assert client.put(path, headers=admin, json={'suspended': 'yes'}).status_code == 422
    assert client.put(BASE + '/tenants/' + uid() + '/suspension', headers=admin,
                      json={'suspended': True}).status_code == 404
    for _ in range(2):
        assert client.put(path, headers=admin, json={'suspended': True}).status_code == 200
    assert client.get('/api/v1/tenant', headers=tenant['headers']).status_code == 403
    assert client.post(base, json={'message': 'Hello'}).status_code == 403
    assert client.get(base + '/widget/config').status_code == 403
    assert not usage.reserve(tenant['tenant_id'], 'no-charge')
    handle(tenant['tenant_id'], {'phone_number_id': 'number', 'from': '15551234567', 'type': 'text', 'text': 'Hello'})
    assert process_turn()
    with Session() as db:
        assert db.get(WidgetTurn, turn['id']).state == 'failed'
        assert db.get(Tenant, tenant['tenant_id']).bot_live
        rows = db.scalars(select(AuditLog).where(AuditLog.action == 'tenant.suspended')).all()
        assert len(rows) == 1 and rows[0].actor_type == 'platform_admin'
    assert not sent and not chats['openai'].calls
    client.cookies.clear()
    client.cookies.update(user_cookies)
    assert client.get('/api/v1/auth/me').status_code == 403
    credentials = {'email': 'member@example.com', 'password': 'strong-passphrase-123'}
    assert client.post('/api/v1/auth/login', json=credentials).status_code == 403
    client.cookies.clear()
    client.cookies.update(admin_cookies)
    assert client.put(path, headers=admin, json={'suspended': False}).status_code == 200
    assert client.get('/api/v1/tenant', headers=tenant['headers']).status_code == 200
    client.cookies.clear()
    assert client.post('/api/v1/auth/login', json=credentials).status_code == 200


def test_member_of_an_active_workspace_can_still_login(client, tenant, admin):
    headers = login(client, 'multi@example.com', email.sender)
    active = client.post('/api/v1/workspaces', headers=headers, json={'name': 'Active workspace'}).json()['tenant_id']
    with Session.begin() as db:
        user = db.scalar(select(User).where(User.email == 'multi@example.com'))
        db.add(Membership(user_id=user.id, tenant_id=tenant['tenant_id'], role='viewer'))
        db.get(Tenant, tenant['tenant_id']).suspended = True
    assert client.post('/api/v1/auth/login', json={'email': 'multi@example.com',
                                                 'password': 'strong-passphrase-123'}).status_code == 200
    assert [row['tenant_id'] for row in client.get('/api/v1/workspaces').json()] == [active]
    assert client.get('/api/v1/tenant', headers={'X-Workspace-Id': tenant['tenant_id']}).status_code == 403
    assert client.get('/api/v1/tenant', headers={**tenant['headers'], 'X-Workspace-Id': active}).status_code == 404


def test_suspension_during_generation_discards_bot_replies(client, tenant, sent, monkeypatch):
    original = rag.answer

    def answer(*args, **kwargs):
        with Session.begin() as db:
            db.get(Tenant, tenant['tenant_id']).suspended = True
        return original(*args, **kwargs)

    monkeypatch.setattr(rag, 'answer', answer)
    turn = client.post('/api/v1/chat/' + tenant['public_key'] + '/widget/turns',
                       json={'request_id': uid(), 'message': 'Hello'}).json()
    assert process_turn()
    with Session.begin() as db:
        assert db.get(WidgetTurn, turn['id']).state == 'failed'
        db.get(Tenant, tenant['tenant_id']).suspended = False
    handle(tenant['tenant_id'], {'id': 'during-suspension', 'phone_number_id': 'number',
                                'from': '15551234567', 'type': 'text', 'text': 'Hello'})
    assert not sent
