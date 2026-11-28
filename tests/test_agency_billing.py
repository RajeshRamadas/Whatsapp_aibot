from uuid import uuid4

from sqlalchemy import select

from app.config import settings
from app.db import AuditLog, Plan, Session, Subscription, UsageEvent
from app.services import email, usage
from tests.test_workspaces import Recorder, login

BASE = '/api/v1/agency'


def workspaces(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    recorder = Recorder()
    monkeypatch.setattr(email, 'sender', recorder)
    csrf = login(client, 'agency-owner@example.com', recorder)
    def create(name):
        response = client.post('/api/v1/workspaces', headers=csrf, json={'name': name})
        assert response.status_code == 201
        return response.json()['tenant_id']
    payer, managed = create('Agency'), create('Client')
    return payer, managed, {**csrf, 'X-Workspace-Id': payer}, {**csrf, 'X-Workspace-Id': managed}


def test_agency_pool_blocks_duplicate_checkout_and_keeps_attribution(client, monkeypatch):
    payer, managed, headers, child = workspaces(client, monkeypatch)
    with Session.begin() as db:
        db.get(Plan, 'starter').limits = {'ai_messages': 2}
    path = f'{BASE}/clients/{managed}'
    assert client.get(f'{BASE}/billing', headers=headers).json()['candidates'][0]['id'] == managed
    assert client.put(path, headers=headers).status_code == 200
    assert client.put(path, headers=headers).status_code == 200
    status = client.get('/api/v1/billing', headers=child).json()
    assert status['managed_by'] == 'Agency' and status['subscription'] is None
    assert client.post('/api/v1/billing/checkout', headers=child, json={'plan': 'starter'}).status_code == 409
    assert client.put('/api/v1/usage/quota-policy', headers=child, json={'behavior': 'overage'}).status_code == 409
    assert usage.reserve(payer, 'agency-turn') and usage.reserve(managed, 'client-turn')
    assert usage.reserve(managed, 'client-turn') and not usage.reserve(managed, 'quota-exhausted')
    assert not usage.reserve(payer, 'also-blocked')
    stats = client.get(f'{BASE}/billing', headers=headers).json()
    assert stats['ai_messages'] == 2 and stats['clients'][0]['ai_messages'] == 1
    assert client.delete(path, headers=headers).status_code == 204
    assert client.get(f'{BASE}/billing', headers=headers).json()['ai_messages'] == 2
    assert usage.reserve(managed, 'direct-after-unlink')
    with Session() as db:
        row = db.scalar(select(UsageEvent).where(UsageEvent.request_key == 'client-turn'))
        assert row.tenant_id == managed and row.billing_tenant_id == payer
        assert len(db.scalars(select(AuditLog).where(AuditLog.tenant_id == payer,
            AuditLog.action == 'agency.client_linked')).all()) == 1


def test_agency_permissions_and_live_client_subscription(client, tenant, monkeypatch):
    payer, managed, headers, child = workspaces(client, monkeypatch)
    assert client.get(f'{BASE}/billing', headers=tenant['headers']).status_code == 403
    assert client.put(f'{BASE}/clients/invalid', headers=headers).status_code == 422
    assert client.put(f'{BASE}/clients/{payer}', headers=headers).status_code == 422
    assert client.put(f"{BASE}/clients/{tenant['tenant_id']}", headers=headers).status_code == 404
    assert client.delete(f'{BASE}/clients/{uuid4()}', headers=headers).status_code == 404
    with Session.begin() as db:
        db.add(Subscription(tenant_id=managed, plan_id='starter', status='creating', checkout_key=str(uuid4())))
    assert client.put(f'{BASE}/clients/{managed}', headers=headers).status_code == 409
    with Session.begin() as db:
        db.get(Subscription, managed).status = 'cancelled'
    assert client.put(f'{BASE}/clients/{managed}', headers=headers).status_code == 200
    assert client.put(f'{BASE}/clients/{payer}', headers=child).status_code == 409
    assert client.get(f'{BASE}/billing', headers=child).status_code == 409
    client.cookies.clear()
    assert client.get(f'{BASE}/billing', headers=headers).status_code == 401
