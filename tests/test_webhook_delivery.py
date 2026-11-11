"""Exact-byte signatures, bounded retries, lease recovery and safe failure handling."""
import asyncio
import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest
from redis.exceptions import ConnectionError
from sqlalchemy import select

from app.db import Membership, Session, User, engine, now, uid
from app.integrations.outgoing_webhooks import DeliveryError, Transport, body_bytes, signature
from app.models.webhooks import WebhookDelivery, WebhookEndpoint
from app.rate_limits import RateLimiter
from app.services import email, web_fetch, webhook_delivery
from app.services.outgoing_webhooks import stage
from tests import test_outgoing_webhooks as fixtures

endpoint = fixtures.endpoint


@pytest.fixture
def delivery(client, tenant, endpoint):
    fixtures.activate(client, tenant, endpoint)
    with Session.begin() as db:
        stage(db, tenant['tenant_id'], 'lead.created', 'lead:1', {'conversation_id': uid()})
        return db.scalar(select(WebhookDelivery)).id


class FakeTransport:
    def __init__(self, code=204, delay=0, failure=None):
        self.code, self.delay, self.failure, self.calls = code, delay, failure, []

    async def send(self, url, raw, headers):
        assert engine.pool.checkedout() == 0
        self.calls.append((raw, headers))
        if self.failure:
            raise self.failure
        return self.code, self.delay


def run(transport):
    return asyncio.run(webhook_delivery.process_one(transport))


def test_signed_delivery_and_replay(client, tenant, endpoint, delivery):
    transport = FakeTransport()
    assert run(transport) and not run(transport)
    raw, headers = transport.calls[0]
    timestamp = int(headers['X-Signature'].split(',')[0].split('=')[1])
    expected = hmac.new(endpoint['secret'].encode(), str(timestamp).encode() + b'.' + raw, hashlib.sha256).hexdigest()
    assert headers['X-Signature'] == f't={timestamp},v1={expected}'
    assert headers['X-Event-Id'] == json.loads(raw)['id']
    assert signature('secret', timestamp, raw + b' ') != signature('secret', timestamp, raw)
    with Session() as db:
        row = db.get(WebhookDelivery, delivery)
        assert row.state == 'delivered' and row.attempts == 1 and row.response_code == 204
        assert endpoint['secret'] not in json.dumps(row.history)
        assert body_bytes(row.payload) == raw
        assert db.get(WebhookEndpoint, endpoint['id']).last_success_at


@pytest.mark.parametrize(('code', 'expected'), [(500, 'queued'), (408, 'queued'), (429, 'queued'),
                                               (302, 'failed'), (400, 'failed'), (401, 'failed')])
def test_retry_policy(client, tenant, endpoint, delivery, code, expected):
    transport = FakeTransport(code, delay=120)
    assert run(transport)
    with Session.begin() as db:
        row = db.get(WebhookDelivery, delivery)
        assert row.state == expected and row.next_attempt_at >= now() + timedelta(seconds=115)
        assert db.get(WebhookEndpoint, endpoint['id']).failure_since
        if expected == 'queued':
            row.next_attempt_at = now() - timedelta(seconds=1)
    if expected == 'queued':
        assert run(FakeTransport())
        with Session() as db:
            assert db.get(WebhookEndpoint, endpoint['id']).failure_since is None


def test_expiry_disable_and_retention(client, tenant, endpoint, delivery, monkeypatch):
    assert run(FakeTransport(failure=DeliveryError('receiver_unavailable')))
    with Session.begin() as db:
        db.get(WebhookDelivery, delivery).expires_at = now() - timedelta(seconds=1)
        db.get(WebhookEndpoint, endpoint['id']).failure_since = now() - timedelta(days=3, seconds=1)
        user = User(id=uid(), email='webhook-owner@example.com', name='Owner', password_hash='unused')
        db.add(user)
        db.flush()
        db.add(Membership(tenant_id=tenant['tenant_id'], user_id=user.id, role='owner'))
    webhook_delivery.maintain()
    notified = []
    monkeypatch.setattr(email.sender, 'stage', lambda db, *args: notified.append(args))
    assert webhook_delivery.notify_one() and not webhook_delivery.notify_one()
    assert len(notified) == 1 and notified[0][1] == 'Webhook destination disabled'
    with Session.begin() as db:
        ep = db.get(WebhookEndpoint, endpoint['id'])
        assert not ep.enabled and ep.disabled_reason == 'continuous_failure'
        row = db.get(WebhookDelivery, delivery)
        assert row.state == 'failed'
        row.created_at = now() - timedelta(days=31)
    webhook_delivery.maintain()
    with Session() as db:
        assert db.get(WebhookDelivery, delivery) is None


def test_rate_outage_and_stale_lease(client, tenant, endpoint, delivery, monkeypatch):
    transport = FakeTransport()
    def unavailable(*args):
        assert engine.pool.checkedout() == 0
        raise ConnectionError('private URL')
    monkeypatch.setattr(RateLimiter, 'allow', unavailable)
    assert run(transport) and not transport.calls
    with Session.begin() as db:
        row = db.get(WebhookDelivery, delivery)
        assert row.attempts == 0 and row.last_error == 'quota_unavailable'
        row.state, row.claimed_at = 'sending', now() - timedelta(minutes=3)
        row.next_attempt_at = now() - timedelta(seconds=1)
    monkeypatch.setattr(RateLimiter, 'allow', lambda *a: True)
    assert run(transport) and len(transport.calls) == 1
    with Session() as db:
        assert db.get(WebhookDelivery, delivery).state == 'delivered'


def test_transport_pins_dns_and_rejects_rebinding(monkeypatch):
    connections = []
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        @asynccontextmanager
        async def stream(self, method, target, **kwargs):
            assert method == 'POST' and target.host == '93.184.216.34'
            assert kwargs['headers']['Host'] == 'hooks.example.com' and kwargs['timeout'] == 10
            connections.append(target)
            yield httpx.Response(302, headers={'Location': 'https://127.0.0.1/', 'Retry-After': '120'})
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    monkeypatch.setattr(web_fetch, 'resolve', lambda host: '93.184.216.34')
    assert asyncio.run(Transport().send('https://hooks.example.com/hook', b'{}', {})) == (302, 120)
    def rebound(host):
        raise web_fetch.FetchError('private address')
    monkeypatch.setattr(web_fetch, 'resolve', rebound)
    with pytest.raises(DeliveryError) as error:
        asyncio.run(Transport().send('https://hooks.example.com/hook', b'{}', {}))
    assert error.value.code == 'unsafe_destination' and not error.value.retryable
    assert len(connections) == 1


def test_dead_letter_and_expired_window(client, tenant, endpoint, delivery):
    from app.api.platform import failed_jobs
    with Session.begin() as db:
        db.get(WebhookDelivery, delivery).expires_at = now() - timedelta(seconds=1)
    webhook_delivery.maintain()
    assert not run(FakeTransport())
    with Session() as db:
        assert db.get(WebhookDelivery, delivery).last_error == 'retry_window_expired'
    # The platform-wide queue exposes IDs/types, never destination secrets or request content.
    result = failed_jobs(limit=25, offset=0)
    assert any(row.kind == 'outgoing_webhook' and row.id == delivery for row in result.items)
