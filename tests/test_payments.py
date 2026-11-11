"""Merchant credential isolation, authorization and approved-amount invariants."""
import base64
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import ApiKey, Conversation, Session, engine, now, uid
from app.integrations.payment_links import Razorpay
from app.models.payments import PaymentConnection as Connection
from app.services.auth import hash_token

BASE = '/api/v1/payments'
SECRETS = {'label': 'Demo merchant', 'key_id': 'rzp_test_demo1234', 'key_secret': 'merchant-secret',
           'webhook_secret': 'merchant-webhook-secret'}


@pytest.fixture
def merchant(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b'p' * 32).decode())
    calls = []
    def fake(self, method, path='', **kwargs):
        assert engine.pool.checkedout() == 0
        assert self.credentials['key_secret'] == SECRETS['key_secret']
        calls.append((method, path, kwargs))
        return {'items': []}
    monkeypatch.setattr(Razorpay, 'request', fake)
    response = client.post(BASE + '/connections', headers=tenant['headers'], json=SECRETS)
    assert response.status_code == 201, response.text
    connection = response.json()
    assert connection['status'] == 'needs_test' and connection['key_last4'] == '1234'
    assert client.post(BASE + '/connections/' + connection['id'] + '/test',
                       headers=tenant['headers']).json()['status'] == 'connected'
    with Session.begin() as db:
        conversation = uid()
        db.add(Conversation(id=conversation, tenant_id=tenant['tenant_id'], channel='web',
                            external_user='Demo customer'))
    body = {'connection_id': connection['id'], 'conversation_id': conversation, 'approval_key': uid(),
            'amount': 125050, 'description': 'Booking deposit', 'customer_name': 'Demo customer',
            'customer_phone': '+919999000001', 'expires_at': (now() + timedelta(days=1)).isoformat()}
    return connection, body, calls


def test_encrypted_merchant_and_approved_idempotent_dues(client, tenant, merchant):
    c, body, calls = merchant
    headers = tenant['headers']
    result = client.post(BASE + '/requests', headers=headers, json=body)
    assert result.status_code == 201, result.text
    value = result.json()
    assert value['state'] == 'approved' and value['amount'] == 125050
    assert client.post(BASE + '/requests', headers=headers, json=body).json()['id'] == value['id']
    assert client.post(BASE + '/requests', headers=headers, json={**body, 'amount': 999}).status_code == 409
    assert client.get(BASE + '/requests', headers=headers).json()[0]['id'] == value['id']
    for action in ('send', 'sync', 'cancel'):
        response = client.post(BASE + '/requests/' + value['id'] + '/' + action,
                                headers=headers, json={'confirm': True})
        assert response.status_code == 200, response.text
    assert len(calls) == 1  # Only the explicit connection test called the provider.
    result = client.get(BASE + '/connections', headers=headers)
    assert 'merchant-secret' not in result.text and 'credentials_enc' not in result.text
    with Session() as db:
        row = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        assert b'merchant-secret' not in row.credentials_enc and b'merchant-webhook' not in row.webhook_secret_enc
    assert client.request('DELETE', BASE + '/connections/' + c['id'], headers=headers,
                          json={'confirm': True}).json()['status'] == 'disconnected'


def test_payment_validation_roles_and_tenant_isolation(client, tenant, merchant):
    c, body, _ = merchant
    headers = tenant['headers']
    value = client.post(BASE + '/requests', headers=headers, json=body).json()
    for bad in (True, 1.2, '100', 0, 100000001):
        assert client.post(BASE + '/requests', headers=headers, json={**body, 'amount': bad}).status_code == 422
    assert client.post(BASE + '/connections', headers=headers,
                       json={**SECRETS, 'key_id': 'rzp_live_demo1234'}).status_code == 422
    assert client.post(BASE + '/requests', headers=headers,
                       json={**body, 'approval_key': uid(), 'conversation_id': uid()}).status_code == 404
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other merchant'}).json()
    foreign = {'Authorization': 'Bearer ' + other['admin_key']}
    assert client.get(BASE + '/connections', headers=foreign).json() == []
    assert client.get(BASE + '/requests', headers=foreign).json() == []
    assert client.get(BASE + '/catalog', headers=foreign).json()['connections'] == []
    assert client.post(BASE + '/requests', headers=foreign, json=body).status_code == 404
    routes = [('POST', '/connections/' + c['id'] + '/test', None),
              ('DELETE', '/connections/' + c['id'], {'confirm': True}),
              ('POST', '/requests/' + value['id'] + '/send', {'confirm': True})]
    for method, path, payload in routes:
        assert client.request(method, BASE + path, headers=foreign, json=payload).status_code == 404
        assert client.request(method, BASE + path, json=payload).status_code == 401
    for method, path, payload in routes + [('GET', '/connections', None), ('POST', '/connections', SECRETS),
                                          ('GET', '/requests', None), ('GET', '/catalog', None),
                                          ('POST', '/requests', body)]:
        key = uid()
        with Session.begin() as db:
            db.add(ApiKey(tenant_id=tenant['tenant_id'], key_hash=hash_token(key), key_prefix=key[:8],
                          name='Reader', scopes=['viewer']))
        assert client.request(method, BASE + path, headers={'Authorization': 'Bearer ' + key},
                              json=payload).status_code == 403
        assert client.request(method, BASE + path, json=payload).status_code == 401
    assert client.post(BASE + '/connections/invalid/test', headers=headers).status_code == 422
    assert client.request('DELETE', BASE + '/connections/' + c['id'], headers=headers,
                          json={'confirm': False}).status_code == 422


def test_payment_catalog_is_scoped_and_does_not_expose_credentials(client, tenant, merchant):
    connection, _, _ = merchant
    response = client.get(BASE + '/catalog', headers=tenant['headers'])
    assert response.status_code == 200 and response.json()['connections'][0]['id'] == connection['id']
    assert 'key_last4' not in response.text and 'merchant-secret' not in response.text
    assert client.get(BASE + '/catalog').status_code == 401
    assert client.get(BASE + '/catalog', params={'unused': 'ignored'}, headers=tenant['headers']).status_code == 200
