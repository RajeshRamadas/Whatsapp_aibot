import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import rag
from app.config import settings
from app.db import AnalyticsDaily, Conversation, Lead, Message, ProductEvent, Session, Tenant, uid
from app.jobs.worker import reconcile_analytics
from app.services import analytics, email
from tests.test_workspaces import Recorder, login

BASE = '/api/v1/analytics'
AT = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
RANGE = '?from_date=2026-10-01&to_date=2026-10-02'


def seed(tenant_id):
    with Session.begin() as db:
        conversations = [Conversation(id=uid(), tenant_id=tenant_id, channel=channel, created_at=AT)
                         for channel in ('web', 'web', 'whatsapp')]
        db.add_all(conversations)
        db.flush()
        for conv in conversations:
            db.add(Message(conversation_id=conv.id, role='user', content='Private customer text', created_at=AT))
        db.add_all([
            Message(conversation_id=conversations[0].id, role='assistant', content='Private reply',
                    created_at=AT + timedelta(seconds=10)),
            Message(conversation_id=conversations[0].id, role='assistant', content='Next day',
                    created_at=AT + timedelta(days=1)),
            Message(conversation_id=conversations[1].id, role='agent', content='Agent reply',
                    delivery_status='sent', created_at=AT + timedelta(seconds=30)),
            Message(conversation_id=conversations[1].id, role='agent', content='Not delivered',
                    delivery_status='queued', created_at=AT + timedelta(seconds=1)),
            Message(conversation_id=conversations[1].id, role='assistant', content='Failed delivery',
                    delivery_status='failed', created_at=AT + timedelta(seconds=2)),
            Message(conversation_id=conversations[2].id, role='agent', kind='note', content='Internal note',
                    created_at=AT + timedelta(seconds=1)),
            Lead(tenant_id=tenant_id, conversation_id=conversations[0].id,
                 fields={'phone': '15551234567'}, created_at=AT),
        ])
        conversations[1].handoff, conversations[1].status = True, 'agent'
    return conversations


def test_daily_metrics_weighting_channels_and_idempotency(client, tenant):
    conversations = seed(tenant['tenant_id'])
    asyncio.run(reconcile_analytics({}))
    analytics.reconcile()
    response = client.get(BASE + RANGE, headers=tenant['headers'])
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['updated_at']
    totals = data['summary']
    assert (totals['conversations'], totals['messages'], totals['leads']) == (3, 6, 1)
    assert (totals['bot_answered'], totals['handed_off'], totals['first_response_seconds']) == (1, 1, 20)
    assert totals['bot_share'] == pytest.approx(100 / 3)
    assert totals['handoff_rate'] == pytest.approx(100 / 3)
    assert len(data['daily']) == 3
    web = client.get(BASE + RANGE + '&channel=web', headers=tenant['headers']).json()['summary']
    assert (web['conversations'], web['messages'], web['bot_share']) == (2, 5, 50)
    wa = client.get(BASE + RANGE + '&channel=whatsapp', headers=tenant['headers']).json()['summary']
    assert wa['conversations'] == 1 and wa['first_response_seconds'] is None
    assert 'Private' not in response.text and '15551234567' not in response.text
    with Session.begin() as db:
        conv = db.get(Conversation, conversations[0].id)
        conv.handoff, conv.status = True, 'waiting'
    with Session.begin() as db:
        conv = db.get(Conversation, conversations[0].id)
        conv.handoff, conv.status = False, 'bot'
    analytics.reconcile()
    totals = client.get(BASE + RANGE, headers=tenant['headers']).json()['summary']
    assert totals['bot_answered'] == 0 and totals['handed_off'] == 2
    with Session.begin() as db:
        for conv in conversations:
            db.delete(db.get(Conversation, conv.id))
    analytics.reconcile()
    assert client.get(BASE + RANGE, headers=tenant['headers']).json()['daily'] == []


def test_analytics_auth_isolation_empty_and_validation(client, tenant):
    assert client.get(BASE).status_code == 401
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other workspace'}).json()
    seed(other['tenant_id'])
    analytics.reconcile()
    empty = client.get(BASE + RANGE + '&tenant_id=' + other['tenant_id'], headers=tenant['headers']).json()
    assert empty['summary']['conversations'] == 0 and empty['daily'] == []
    assert empty['summary']['first_response_seconds'] is None
    assert client.get(BASE, headers={**tenant['headers'], 'X-Workspace-Id': other['tenant_id']}).status_code == 404
    for query in ('?channel=other', '?from_date=bad', '?from_date=2026-10-02&to_date=2026-10-01',
                  '?from_date=2025-01-01&to_date=2026-01-01'):
        assert client.get(BASE + query, headers=tenant['headers']).status_code == 422
    with Session.begin() as db:
        db.get(Tenant, tenant['tenant_id']).suspended = True
    assert client.get(BASE, headers=tenant['headers']).status_code == 403


def test_midnight_cohorts_keep_reply_day_and_response_duration(client, tenant):
    at = AT.replace(hour=23, minute=59, second=50)
    with Session.begin() as db:
        conv = Conversation(id=uid(), tenant_id=tenant['tenant_id'], channel='web')
        db.add(conv)
        db.flush()
        db.add_all([Message(conversation_id=conv.id, role='user', content='Private', created_at=at),
                    Message(conversation_id=conv.id, role='assistant', content='Private',
                            created_at=at + timedelta(seconds=20))])
    analytics.reconcile()
    data = client.get(BASE + RANGE, headers=tenant['headers']).json()
    assert [(row['day'], row['conversations'], row['messages']) for row in data['daily']] == [
        ('2026-10-01', 1, 1), ('2026-10-02', 0, 1)]
    assert data['summary']['first_response_seconds'] == 20


def test_milestones_are_atomic_private_and_deduplicated(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    monkeypatch.setattr(email, 'sender', Recorder())
    headers = login(client, 'private@example.com', email.sender)
    workspace = client.post('/api/v1/workspaces', headers=headers, json={'name': 'Analytics'}).json()
    tenant_id = workspace['tenant_id']
    headers['X-Workspace-Id'] = tenant_id
    client.patch('/api/v1/tenant', headers=headers, json={'whatsapp_phone_number_id': 'secret-number'})
    client.post('/api/v1/sources', headers=headers, files={'file': ('policy.txt', b'Refund policy', 'text/plain')})
    for _ in range(2):
        assert client.post('/api/v1/bot/test', headers=headers, json={'message': 'Private preview'}).status_code == 200
        assert client.post('/api/v1/bot/live', headers=headers, json={'live': True, 'confirm': True}).status_code == 200
    with Session.begin() as db:
        conv = Conversation(id=uid(), tenant_id=tenant_id, channel='web')
        db.add(conv)
        db.flush()
        rag.save_exchange_rows(db, conv, [('user', 'Private customer text')], lead_fields={'phone': '15551234567'})
        rag.save_exchange_rows(db, conv, [('user', 'More private text')], lead_fields={'phone': '15551234567'})
    data = client.get(BASE, headers=headers).json()
    assert all(data['milestones'].get(name) == 1 for name in (
        'signup_completed', 'workspace_created', 'whatsapp_connected', 'first_knowledge_ready',
        'first_test_message', 'went_live', 'first_customer_conversation', 'first_lead',
        'onboarding_whatsapp_completed', 'onboarding_knowledge_completed', 'onboarding_test_completed',
        'onboarding_live_completed'))
    with Session() as db:
        rows = db.scalars(select(ProductEvent)).all()
        assert sum(row.name == 'signup_started' for row in rows) == 1
        persisted = str([(row.name, row.event_key) for row in rows])
        private = ('Private', 'private@example.com', '15551234567', 'secret-number')
        assert all(value not in persisted for value in private)
    with pytest.raises(ValueError), Session.begin() as db:
        analytics.record(db, 'arbitrary message text', tenant_id)
    with Session() as db:
        assert db.scalars(select(AnalyticsDaily)).all() == []
