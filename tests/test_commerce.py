"""No provider keys/network; real database proves workspace isolation and identity binding."""
import base64

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import ApiKey, Conversation, Session, engine, uid
from app.integrations.commerce import Provider, StoreInput
from app.integrations.http_actions.transport import ActionError
from app.models.commerce import Store
from app.services import commerce
from app.services.auth import hash_token

BASE = '/api/v1/commerce'
INPUT = {'provider': 'shopify', 'url': 'https://demo.myshopify.com', 'token': 'store-private-secret',
         'read_only': True}


@pytest.fixture
def store(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b'c' * 32).decode())
    async def test(self):
        assert engine.pool.checkedout() == 0
        assert self.credentials['token'] == INPUT['token']
    monkeypatch.setattr(Provider, 'test', test)
    result = client.post(BASE, headers=tenant['headers'], json=INPUT)
    assert result.status_code == 201, result.text
    identity = result.json()['id']
    assert client.post(f'{BASE}/{identity}/test', headers=tenant['headers']).json()['status'] == 'connected'
    return identity


def test_store_lifecycle_secrets_and_auth(client, tenant, store):
    headers = tenant['headers']
    assert client.post(BASE, headers=headers, json=INPUT).status_code == 409
    response = client.get(BASE, headers=headers)
    assert len(response.json()) == 1 and 'store-private-secret' not in response.text
    with Session() as db:
        row = db.scalar(select(Store).where(Store.tenant_id == tenant['tenant_id']))
        assert INPUT['token'].encode() not in row.credentials_enc
    foreign = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                          json={'name': 'Other store'}).json()
    other = {'X-Admin-Key': foreign['admin_key']}
    assert client.get(BASE, headers=other).json() == []
    for method, path, payload in [('GET', '', None), ('POST', '', INPUT),
                                  ('POST', f'/{store}/test', None), ('DELETE', f'/{store}', None)]:
        assert client.request(method, BASE + path, json=payload).status_code == 401
        key = uid()
        with Session.begin() as db:
            db.add(ApiKey(tenant_id=tenant['tenant_id'], key_hash=hash_token(key), key_prefix=key[:8],
                          name='Reader', scopes=['viewer']))
        assert client.request(method, BASE + path, headers={'X-Admin-Key': key}, json=payload).status_code == 403
        if path:
            assert client.request(method, BASE + path, headers=other, json=payload).status_code == 404
    assert client.post(BASE + '/invalid/test', headers=headers).status_code == 422
    assert client.delete(BASE + '/invalid', headers=headers).status_code == 422
    assert client.delete(f'{BASE}/{store}', headers=headers).status_code == 204
    assert client.get(BASE, headers=headers).json() == []


@pytest.mark.parametrize('change', [dict(url='http://demo.myshopify.com'), dict(url='https://127.0.0.1'),
    dict(url='https://demo.myshopify.com/path'), dict(url='https://demo.myshopify.com/?q=1'),
    dict(url='https://evil.example'), dict(read_only=False), dict(read_only='true'), dict(token='abc'),
    dict(provider='other'), dict(tenant_id='untrusted'), dict(token='secret\r\nheader')])
def test_store_validation(client, tenant, change):
    assert client.post(BASE, headers=tenant['headers'], json=INPUT | change).status_code == 422


def test_order_owner_is_channel_bound(client, tenant, store, monkeypatch):
    tid = tenant['tenant_id']
    with Session.begin() as db:
        wa, web = uid(), uid()
        db.add_all([Conversation(id=wa, tenant_id=tid, channel='whatsapp', external_user='919999000001'),
                    Conversation(id=web, tenant_id=tid, channel='web', external_user='919999000001')])
    calls = []
    async def order(self, number):
        assert engine.pool.checkedout() == 0
        calls.append(number)
        return {'phones': ['+919999000001'], 'number': number, 'status': 'paid', 'items': []}
    monkeypatch.setattr(Provider, 'order', order)
    assert commerce.lookup(tid, store, web, '1001', '+919999000001') is None
    assert commerce.lookup(tid, store, wa, '1001', '+919999000002') is None
    assert calls == []
    result = commerce.lookup(tid, store, wa, '1001', '+919999000001')
    assert result['status'] == 'paid' and 'phones' not in result
    async def mismatch(self, number):
        return {'phones': ['+919999000002'], 'private': 'never released'}
    monkeypatch.setattr(Provider, 'order', mismatch)
    assert commerce.lookup(tid, store, wa, '1001', '919999000001') is None
    with pytest.raises(ActionError):
        commerce.lookup(uid(), store, wa, '1001', '919999000001')


def test_failed_store_test_is_sanitized(client, tenant, store, monkeypatch):
    async def fail(self):
        raise ActionError('auth_failed')
    monkeypatch.setattr(Provider, 'test', fail)
    response = client.post(f'{BASE}/{store}/test', headers=tenant['headers'])
    assert response.json()['last_error'] == 'auth_failed'


def test_provider_fixed_read_only_operations():
    import asyncio
    from types import SimpleNamespace
    calls = []
    class Transport:
        async def request(self, method, url, headers, **kwargs):
            calls.append((method, url, headers, kwargs))
            return 200, {'data': {'currentAppInstallation': {
                'accessScopes': [{'handle': s} for s in ('read_orders', 'read_products')]}}}
    values = StoreInput(**INPUT)
    provider = Provider(SimpleNamespace(provider=values.provider, url=values.url),
                        {'token': INPUT['token'], 'consumer_key': ''}, Transport())
    asyncio.run(provider.test())
    assert calls[0][0] == 'POST' and '/2026-10/graphql.json' in calls[0][1]
    assert 'mutation' not in calls[0][3]['body']['query']
    with pytest.raises(ActionError, match='read_only_required'):
        asyncio.run(provider.request(query='mutation { delete }'))
    with pytest.raises(ActionError, match='invalid_order'):
        asyncio.run(provider.order('1001 OR phone:*'))
