"""Provider uncertainty, cancellation races, verified captures and webhook isolation."""
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import Session, engine, now, uid
from app.integrations.payment_links import PaymentError, Razorpay
from app.models.payments import PaymentEvent as Event
from app.models.payments import PaymentRequest as Request
from app.services import payment_processing as worker
from app.services import payments
from tests.test_payments import BASE, SECRETS
from tests.test_payments import merchant as merchant


@pytest.fixture
def payment(client, tenant, merchant, monkeypatch):
    c, body, calls = merchant
    response = client.post(BASE + '/requests', headers=tenant['headers'], json=body)
    value = response.json()
    remote = {}
    posted = []
    original = Razorpay.request
    def fake(self, method, path='', **kwargs):
        assert engine.pool.checkedout() == 0
        if method == 'POST' and not path:
            posted.append(kwargs['payload'])
            data = kwargs['payload']
            remote.update({**data, 'id': 'plink_demo123', 'amount_paid': 0, 'status': 'created',
                           'short_url': 'https://rzp.io/i/demo'})
            return dict(remote)
        if method == 'POST' and path.endswith('/cancel'):
            remote['status'] = 'cancelled'
            return dict(remote)
        if path:
            return dict(remote)
        if kwargs.get('params', {}).get('reference_id'):
            return {'items': [dict(remote)] if remote else []}
        return original(self, method, path, **kwargs)
    monkeypatch.setattr(Razorpay, 'request', fake)
    client.post(BASE + '/requests/' + value['id'] + '/send', headers=tenant['headers'], json={'confirm': True})
    return c, value, remote, posted


def post_event(client, connection_id, remote, name='payment_link.paid', event_id='evt_one', valid=True):
    raw = json.dumps({'event': name, 'payload': {'payment_link': {'entity': remote}, 'payment': {'entity': {
        'id': 'pay_captured123', 'amount': remote['amount'], 'currency': 'INR', 'status': 'captured'}}}}).encode()
    signature = hmac.new(SECRETS['webhook_secret'].encode(), raw, hashlib.sha256).hexdigest()
    return client.post(BASE + '/webhook/' + connection_id, content=raw, headers={
        'X-Razorpay-Signature': signature if valid else '0' * 64, 'X-Razorpay-Event-Id': event_id})


def test_create_capture_duplicates_and_out_of_order_webhooks(client, tenant, payment):
    connection, value, remote, posted = payment
    assert worker.process_one() and len(posted) == 1
    assert posted[0]['amount'] == value['amount'] and posted[0]['notify'] == {'sms': False, 'email': False}
    assert posted[0]['accept_partial'] is False
    remote.update(status='paid', amount_paid=value['amount'])
    for _ in range(2):
        assert post_event(client, connection['id'], remote).status_code == 200
    with Session() as db:
        events = db.scalars(select(Event).where(Event.tenant_id == tenant['tenant_id'])).all()
        assert len(events) == 1 and events[0].payload_enc and b'pay_captured123' not in events[0].payload_enc
    assert worker.process_event()
    with Session() as db:
        row = db.get(Request, value['id'])
        assert row.state == 'paid' and row.payment_id == 'pay_captured123'
    remote['status'] = 'expired'
    assert post_event(client, connection['id'], remote, 'payment_link.expired', 'evt_old').status_code == 200
    worker.process_event()
    with Session() as db:
        assert db.get(Request, value['id']).state == 'paid'
    assert client.post(BASE + '/requests/' + value['id'] + '/cancel', headers=tenant['headers'],
                       json={'confirm': True}).json()['state'] == 'paid'


def test_uncertain_post_reconciles_without_duplicate_creation(client, tenant, payment, monkeypatch):
    connection, value, remote, posted = payment
    original = Razorpay.create
    def uncertain(self, payload):
        original(self, payload)
        raise PaymentError('provider_unavailable', retry=True, uncertain=True)
    monkeypatch.setattr(Razorpay, 'create', uncertain)
    assert worker.process_one() and len(posted) == 1
    with Session.begin() as db:
        row = db.get(Request, value['id'])
        assert row.job_state == 'queued' and row.submitted
        row.next_attempt_at = now()
    assert worker.process_one() and len(posted) == 1
    with Session() as db:
        assert db.get(Request, value['id']).state == 'created'


def test_unknown_creation_never_reposts_and_cancel_during_creation(client, tenant, payment, monkeypatch):
    connection, value, remote, posted = payment
    original = Razorpay.create
    def cancelled(self, payload):
        response = original(self, payload)
        payments.action(tenant['tenant_id'], value['id'], 'cancel')
        return response
    monkeypatch.setattr(Razorpay, 'create', cancelled)
    assert worker.process_one()
    worker.process_one()
    assert remote['status'] == 'cancelled'
    with Session.begin() as db:
        row = db.get(Request, value['id'])
        row.state, row.operation, row.job_state, row.next_attempt_at = 'queued', 'create', 'queued', now()
        row.provider_id = None
    remote.clear()
    assert worker.process_one() and len(posted) == 1
    with Session() as db:
        assert db.get(Request, value['id']).last_error == 'creation_unconfirmed'


def test_webhook_signature_mismatch_account_isolation_and_retention(client, tenant, payment):
    connection, value, remote, _ = payment
    worker.process_one()
    assert post_event(client, connection['id'], remote, valid=False).status_code == 401
    assert client.post(BASE + '/webhook/' + connection['id'], content=b'not-json').status_code == 401
    assert post_event(client, uid(), remote).status_code == 401
    valid_raw = b'not-json'
    signature = hmac.new(SECRETS['webhook_secret'].encode(), valid_raw, hashlib.sha256).hexdigest()
    assert client.post(BASE + '/webhook/' + connection['id'], content=valid_raw,
                       headers={'X-Razorpay-Signature': signature}).status_code == 422
    remote.update(status='paid', amount_paid=value['amount'], amount=value['amount'] + 1)
    post_event(client, connection['id'], remote)
    assert worker.process_event()
    with Session.begin() as db:
        row = db.scalar(select(Event).where(Event.tenant_id == tenant['tenant_id']))
        assert row.state == 'failed' and row.last_error == 'payment_details_mismatch'
        row.created_at = now() - timedelta(days=15)
        assert db.get(Request, value['id']).state == 'created'
    worker.expire_payloads()
    with Session() as db:
        assert db.scalar(select(Event).where(Event.tenant_id == tenant['tenant_id'])).payload_enc is None
