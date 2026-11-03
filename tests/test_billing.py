import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api import billing as api
from app.config import settings
from app.db import BillingEvent, Plan, Session, Subscription, Tenant, now
from app.services import billing, usage


class FakeBilling:
    def __init__(self):
        self.remote = None
        self.checkouts = []
        self.changes = []
        self.fetches = 0
        self.settlement_rows = []

    def checkout(self, plan, start, reference):
        self.checkouts.append((plan, start, reference))
        self.remote = {'id': 'sub_test', 'plan_id': plan, 'status': 'created',
                       'short_url': 'https://rzp.io/i/test', 'notes': {'checkout_key': reference}}
        return self.remote.copy()

    def find_checkout(self, reference):
        return self.remote.copy() if self.remote and self.remote['notes']['checkout_key'] == reference else None

    def fetch(self, subscription):
        assert subscription == self.remote['id']
        self.fetches += 1
        return self.remote.copy()

    def change(self, subscription, plan, immediate):
        assert subscription == 'sub_test'
        self.changes.append((plan, immediate))
        if immediate:
            self.remote['plan_id'] = plan
        return self.remote.copy()

    def cancel(self, subscription, at_end):
        assert subscription == 'sub_test'
        assert at_end
        return self.remote.copy()

    def subscription_for_payment(self, payment):
        return 'sub_test' if payment.get('invoice_id') == 'inv_test' else None

    def settlements(self, day):
        return self.settlement_rows if day == now().date() else []


@pytest.fixture
def fake(client, monkeypatch):
    for name in ('razorpay_key_id', 'razorpay_key_secret', 'razorpay_webhook_secret'):
        monkeypatch.setattr(settings, name, 'test-key')
    monkeypatch.setattr(settings, 'razorpay_plan_ids', {'starter': 'plan_starter', 'growth': 'plan_growth'})
    graph = FakeBilling()
    monkeypatch.setattr(api, 'provider', lambda: graph)
    monkeypatch.setattr(billing, 'provider', lambda: graph)
    with Session.begin() as db:
        db.get(Plan, 'starter').limits = {'ai_messages': 1000, 'price_inr': 1999}
        db.add(Plan(id='growth', limits={'ai_messages': 5000, 'price_inr': 5999}))
    return graph


def event(client, name, key, payment=None, valid=True):
    payload = {'subscription': {'entity': {'id': 'sub_test'}}} if name != 'payment.failed' else {}
    if payment:
        payload['payment'] = {'entity': payment}
    raw = json.dumps({'event': name, 'payload': payload}).encode()
    signature = hmac.new(b'test-key', raw, hashlib.sha256).hexdigest()
    return client.post('/webhooks/razorpay', content=raw, headers={
        'X-Razorpay-Signature': signature if valid else 'invalid', 'X-Razorpay-Event-Id': key,
    })


def test_checkout_trial_and_plan_changes(client, fake, tenant):
    headers = tenant['headers']
    body = {'plan': 'starter'}
    status = client.get('/api/v1/billing', headers=headers).json()
    assert status['entitled'] and status['subscription'] is None
    assert client.post('/api/v1/billing/checkout', headers=headers, json=body).json()['checkout_url'].startswith(
        'https://rzp.io/')
    assert client.post('/api/v1/billing/checkout', headers=headers, json=body).status_code == 200
    assert len(fake.checkouts) == 1
    assert fake.checkouts[0][1] > now().timestamp() + 13 * 86400
    fake.remote.update(status='active', current_end=int((now() + timedelta(days=30)).timestamp()))
    assert event(client, 'subscription.activated', 'evt-activate').status_code == 200
    change = '/api/v1/billing/subscription'
    assert client.patch(change, headers=headers, json={'plan': 'growth'}).json()['plan'] == 'growth'
    assert client.patch(change, headers=headers, json=body).json()['plan'] == 'growth'
    assert fake.changes == [('plan_growth', True), ('plan_starter', False)]
    assert client.post(change + '/cancel', headers=headers).status_code == 200
    assert client.get('/api/v1/billing').status_code == 401


def test_signed_events_grace_pause_recovery_and_reconciliation(client, fake, tenant):
    tenant_id = tenant['tenant_id']
    headers = tenant['headers']
    client.post('/api/v1/billing/checkout', headers=headers, json={'plan': 'starter'})
    fake.remote.update(status='active', current_end=int((now() - timedelta(days=1)).timestamp()))
    assert event(client, 'subscription.activated', 'evt-bad', valid=False).status_code == 401
    assert event(client, 'subscription.activated', 'evt-active').status_code == 200
    fetches = fake.fetches
    assert event(client, 'subscription.activated', 'evt-active').status_code == 200
    assert fake.fetches == fetches
    assert event(client, 'payment.failed', 'evt-failed', {'invoice_id': 'inv_test'}).status_code == 200
    assert usage.reserve(tenant_id, 'in-grace')
    fake.remote['status'] = 'pending'
    assert event(client, 'subscription.pending', 'evt-pending').status_code == 200
    fake.remote['status'] = 'halted'
    assert event(client, 'subscription.halted', 'evt-halted').status_code == 200
    with Session.begin() as db:
        db.get(Subscription, tenant_id).grace_until = now() - timedelta(seconds=1)
    assert not usage.reserve(tenant_id, 'blocked')
    fake.remote.update(status='active', current_end=int((now() + timedelta(days=30)).timestamp()))
    assert event(client, 'subscription.charged', 'evt-paid', {'id': 'pay_test', 'amount': 199900}).status_code == 200
    assert usage.reserve(tenant_id, 'recovered')
    fake.settlement_rows = [{'type': 'payment', 'settlement_id': 'setl_test', 'entity_id': 'pay_test',
                             'amount': 199900}]
    billing.reconcile_settlements()
    with Session() as db:
        assert db.get(BillingEvent, 'evt-paid').settled_at is not None
    fake.remote['status'] = 'cancelled'
    assert event(client, 'subscription.cancelled', 'evt-cancelled').status_code == 200
    fake.remote.update(status='active', plan_id='plan_growth')
    billing.reconcile_subscriptions()
    with Session() as db:
        row = db.scalar(select(Subscription))
        assert row.status == 'active' and row.reconcile_issue
        assert db.get(Tenant, tenant_id).plan_id == 'growth'


def test_expired_trial_pauses_ai_without_charging_quota(client, fake, tenant):
    tenant_id = tenant['tenant_id']
    with Session.begin() as db:
        db.get(Tenant, tenant_id).trial_ends_at = now() - timedelta(days=1)
    assert not usage.reserve(tenant_id, 'trial-expired')
    assert usage.blocked_notice(tenant_id) == billing.BILLING_NOTICE
