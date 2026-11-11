"""Tenant-scoped configuration, encrypted secrets and transactional event staging."""
import base64
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.api.shared import sha
from app.config import settings
from app.db import ApiKey, Session, now, uid
from app.models.webhooks import WebhookDelivery, WebhookEndpoint
from app.services import crypto
from app.services.outgoing_webhooks import stage

BASE = '/api/v1/webhook-endpoints'
BODY = {'name': 'Order automation', 'url': 'https://hooks.example.com/events', 'events': ['lead.created']}


@pytest.fixture
def endpoint(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b'w' * 32).decode())
    response = client.post(BASE, headers=tenant['headers'], json=BODY)
    assert response.status_code == 201, response.text
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Reader', key_prefix='read', key_hash=sha('read'),
                     scopes=['viewer']))
    return response.json()


def activate(client, tenant, endpoint):
    result = client.patch(BASE + '/' + endpoint['id'], headers=tenant['headers'],
                          json={'enabled': True, 'confirm_sharing': True})
    assert result.status_code == 200, result.text


def test_registration_and_lifecycle(client, tenant, endpoint):
    assert endpoint['enabled'] is False
    with Session() as db:
        row = db.get(WebhookEndpoint, endpoint['id'])
        assert endpoint['secret'].encode() not in row.secret_enc
        assert crypto.decrypt(tenant['tenant_id'], row.secret_enc).decode() == endpoint['secret']
    listing = client.get(BASE, headers=tenant['headers'])
    assert listing.status_code == 200 and endpoint['secret'] not in listing.text
    assert client.post(BASE, headers=tenant['headers'], json=BODY).status_code == 409
    assert client.patch(BASE + '/' + endpoint['id'], headers=tenant['headers'],
                        json={'enabled': True}).status_code == 422
    activate(client, tenant, endpoint)
    with Session.begin() as db:
        stage(db, tenant['tenant_id'], 'lead.created', 'lead:1', {'conversation_id': uid()})
    assert client.patch(BASE + '/' + endpoint['id'], headers=tenant['headers'],
                        json={'enabled': False}).status_code == 200
    with Session() as db:
        assert db.scalar(select(WebhookDelivery.state)) == 'cancelled'
    assert client.delete(BASE + '/' + endpoint['id'], headers=tenant['headers']).status_code == 204


@pytest.mark.parametrize('url', ['http://example.com', 'https://localhost/x', 'https://127.0.0.1',
    'https://169.254.169.254/latest', 'https://[::1]', 'https://example.com:8443',
    'https://user:secret@example.com', 'https://example.com/x#fragment', 'https://a.internal/x', 'https://[fec0::1]/'])
def test_unsafe_registration(client, tenant, endpoint, url):
    assert client.post(BASE, headers=tenant['headers'], json={**BODY, 'url': url}).status_code == 422


def test_atomic_fanout_and_resend(client, tenant, endpoint):
    activate(client, tenant, endpoint)
    with pytest.raises(RuntimeError), Session.begin() as db:
        stage(db, tenant['tenant_id'], 'lead.created', 'rolled-back', {'conversation_id': uid()})
        raise RuntimeError('rollback')
    with Session.begin() as db:
        stage(db, tenant['tenant_id'], 'lead.created', 'one', {'conversation_id': uid()})
        stage(db, tenant['tenant_id'], 'lead.created', 'one', {'conversation_id': uid()})
        stage(db, tenant['tenant_id'], 'lead.updated', 'other', {})
    url = BASE + '/' + endpoint['id'] + '/deliveries'
    rows = client.get(url, headers=tenant['headers']).json()
    assert len(rows['items']) == 1
    original = rows['items'][0]
    assert original['payload']['id'].startswith('evt_')
    assert original['payload']['tenant_id'] == tenant['tenant_id']
    body = {'confirm': True, 'request_key': uid()}
    first = client.post(url + '/' + original['id'] + '/resend', headers=tenant['headers'], json=body)
    assert first.status_code == 202
    assert first.json()['payload'] == original['payload']
    assert client.post(url + '/' + original['id'] + '/resend', headers=tenant['headers'],
                       json=body).json() == first.json()
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(WebhookDelivery)) == 2
    page = client.get(url + '?limit=1', headers=tenant['headers']).json()
    assert page['next_cursor']
    assert len(client.get(url, headers=tenant['headers'],
                          params={'cursor': page['next_cursor']}).json()['items']) == 1
    with Session.begin() as db:
        db.get(WebhookDelivery, original['id']).created_at = now() - timedelta(days=31)
    assert len(client.get(url, headers=tenant['headers']).json()['items']) == 1


def test_endpoint_auth_isolation_and_validation(client, tenant, endpoint):
    foreign = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                          json={'name': 'Foreign'}).json()
    path = BASE + '/' + endpoint['id']
    with Session.begin() as db:
        db.get(WebhookEndpoint, endpoint['id']).enabled = True
        stage(db, tenant['tenant_id'], 'lead.created', 'one', {})
        delivery = db.scalar(select(WebhookDelivery))
        delivery_id = delivery.id
    cases = [('post', BASE, BODY), ('get', BASE, None), ('patch', path, {'enabled': False}),
             ('delete', path, None), ('get', path + '/deliveries', None),
             ('post', path + '/deliveries/' + delivery_id + '/resend', {'confirm': True, 'request_key': uid()})]
    for method, url, data in cases:
        kwargs = {'json': data} if data else {}
        call = getattr(client, method)
        assert call(url, **kwargs).status_code == 401
        assert call(url, headers={'Authorization': 'Bearer read'}, **kwargs).status_code == 403
        if url not in (BASE,):
            assert call(url, headers={'X-Admin-Key': foreign['admin_key']}, **kwargs).status_code == 404
    assert client.patch(BASE + '/invalid', headers=tenant['headers'], json={'enabled': False}).status_code == 422
    assert client.delete(BASE + '/invalid', headers=tenant['headers']).status_code == 422
    assert client.get(path + '/deliveries?limit=101', headers=tenant['headers']).status_code == 422
    assert client.post(path + '/deliveries/' + delivery_id + '/resend',
                       headers=tenant['headers'], json={}).status_code == 422
    assert client.post(BASE, headers=tenant['headers'], json={**BODY, 'events': ['unknown']}).status_code == 422
