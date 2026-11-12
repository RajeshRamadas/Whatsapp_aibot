"""Store credentials, bounded provider data and atomic searchable catalogues."""
import asyncio
import base64
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import rag
from app.config import settings
from app.db import ApiKey, Chunk, Session, Source, engine, now
from app.integrations.ecommerce import Store
from app.integrations.http_actions.transport import ActionError
from app.models.ecommerce import StoreConnection
from app.services import ecommerce
from app.services.auth import hash_token

BASE = '/api/v1/stores'
BODY = {'provider': 'shopify', 'label': 'Store', 'origin': 'https://demo.myshopify.com',
        'token': 'synthetic-store-secret'}


class FakeStore:
    error = None
    products_data = [{'id': '1', 'title': 'Shipping book', 'description': 'Shipping guide',
                      'currency': 'INR', 'variants': [{'title': 'Paperback', 'price': '99', 'stock': 5}]}]

    async def test(self):
        assert engine.pool.checkedout() == 0
        if self.error:
            raise ActionError(self.error)

    async def products(self, maximum):
        await self.test()
        return self.products_data


@pytest.fixture
def connection(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b'e' * 32).decode())
    fake = FakeStore()
    monkeypatch.setattr(ecommerce, 'store', lambda row: fake)
    response = client.post(BASE, headers=tenant['headers'], json=BODY)
    assert response.status_code == 201, response.text
    return response.json(), fake


def enable(client, tenant, row):
    path = BASE + '/' + row['id']
    assert client.post(path + '/test', headers=tenant['headers']).json()['status'] == 'connected'
    assert client.patch(path, headers=tenant['headers'], json={'enabled': True}).status_code == 200


def test_catalogue_atomic_refresh_pause_failure_and_delete(client, tenant, connection):
    row, fake = connection
    path, tid = BASE + '/' + row['id'], tenant['tenant_id']
    assert client.patch(path, headers=tenant['headers'], json={'enabled': True}).status_code == 409
    assert client.post(path + '/sync', headers=tenant['headers']).status_code == 409
    assert 'synthetic-store-secret' not in client.get(BASE, headers=tenant['headers']).text
    with Session() as db:
        assert b'synthetic-store-secret' not in db.get(StoreConnection, row['id']).credentials_enc
    enable(client, tenant, row)
    assert (tid, row['id']) in ecommerce.pending()
    ecommerce.sync(tid, row['id'])
    matches = rag.retrieve(tid, 'shipping', [0, 1, 0])
    assert matches and '99' in matches[0].content
    with Session() as db:
        current = db.get(StoreConnection, row['id'])
        source_id = current.source_id
        assert current.product_count == 1 and current.last_success_at
    fake.products_data = []
    ecommerce.sync(tid, row['id'])
    assert not rag.retrieve(tid, 'shipping', [0, 1, 0])
    fake.products_data = FakeStore.products_data
    ecommerce.sync(tid, row['id'])
    fake.error = 'store_rejected'
    ecommerce.sync(tid, row['id'])
    with Session() as db:
        assert db.get(Source, source_id).status == 'error'
        assert db.scalar(select(Chunk.id).where(Chunk.source_id == source_id)) is not None
    assert not rag.retrieve(tid, 'shipping', [0, 1, 0])
    fake.error = None
    ecommerce.sync(tid, row['id'])
    with Session.begin() as db:
        db.get(StoreConnection, row['id']).last_success_at = now() - timedelta(hours=25)
    assert not rag.retrieve(tid, 'shipping', [0, 1, 0])
    assert client.patch(path, headers=tenant['headers'], json={'enabled': False}).status_code == 200
    assert not ecommerce.pending()
    assert client.delete(path, headers=tenant['headers']).status_code == 204
    with Session() as db:
        assert db.get(Source, source_id) is None


def test_sync_lease_and_pause_during_io_prevent_publication(client, tenant, connection):
    row, fake = connection
    enable(client, tenant, row)
    async def products(maximum):
        ecommerce.configure(tenant['tenant_id'], row['id'], False)
        return FakeStore.products_data
    fake.products = products
    ecommerce.sync(tenant['tenant_id'], row['id'])
    with Session() as db:
        assert db.get(StoreConnection, row['id']).source_id is None
    ecommerce.configure(tenant['tenant_id'], row['id'], True)
    with Session.begin() as db:
        current = db.get(StoreConnection, row['id'])
        current.lease, current.lease_until = 'busy', now() + timedelta(minutes=5)
    assert not ecommerce.pending()
    assert client.post(BASE + '/' + row['id'] + '/sync', headers=tenant['headers']).status_code == 409


def test_endpoint_boundaries(client, tenant, connection):
    row, _ = connection
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Viewer', key_prefix='read', key_hash=hash_token('read'),
                      scopes=['viewer']))
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    path = BASE + '/' + row['id']
    calls = [('get', BASE, None), ('post', BASE, BODY), ('post', path + '/test', None),
             ('post', path + '/sync', None), ('patch', path, {'enabled': False}), ('delete', path, None)]
    for method, url, body in calls:
        kwargs = {'json': body} if body else {}
        call = getattr(client, method)
        assert call(url, **kwargs).status_code == 401
        assert call(url, headers={'Authorization': 'Bearer read'}, **kwargs).status_code == 403
        if url != BASE:
            assert call(url, headers={'X-Admin-Key': other['admin_key']}, **kwargs).status_code == 404
            assert call(url.replace(row['id'], 'bad'), headers=tenant['headers'], **kwargs).status_code == 422
    assert client.get(BASE, headers={'X-Admin-Key': other['admin_key']}).json() == []
    assert client.patch(path, headers=tenant['headers'], json={}).status_code == 422


@pytest.mark.parametrize('change', [{'origin': 'http://shop.example'}, {'origin': 'https://127.0.0.1'},
    {'origin': 'https://demo.myshopify.com/path'}, {'origin': 'https://evil.example'}, {'token': 'bad\r\nkey'},
    {'key': 'other-key'}, {'provider': 'woocommerce'}, {'label': ' '}])
def test_invalid_connections(client, tenant, connection, change):
    assert client.post(BASE, headers=tenant['headers'], json={**BODY, **change}).status_code == 422


class Receiver:
    def __init__(self, packets):
        self.packets, self.calls = iter(packets), []

    async def request(self, method, url, headers, **kwargs):
        self.calls.append((method, url, headers, kwargs))
        return 200, next(self.packets)


def test_shopify_fixed_queries_scopes_exact_match_and_product_bound():
    scope = {'data': {'currentAppInstallation': {'accessScopes': [
        {'handle': 'read_orders'}, {'handle': 'read_products'}]}}}
    receiver = Receiver([scope])
    store = Store('shopify', BODY['origin'], {'token': BODY['token']}, receiver)
    asyncio.run(store.test())
    assert receiver.calls[0][0] == 'POST' and 'mutation' not in receiver.calls[0][3]['body']['query']
    scope['data']['currentAppInstallation']['accessScopes'].append({'handle': 'write_orders'})
    store.client = Receiver([scope])
    with pytest.raises(ActionError, match='read_only_scopes_required'):
        asyncio.run(store.test())
    row = {'id': '1', 'title': 'Book', 'description': 'Guide', 'status': 'ACTIVE',
           'variants': {'nodes': [{'title': 'Blue', 'price': '19.00', 'inventoryQuantity': 2}],
                        'pageInfo': {'hasNextPage': False}}}
    packet = {'data': {'shop': {'currencyCode': 'INR'}, 'products': {'nodes': [row],
        'pageInfo': {'hasNextPage': False, 'endCursor': 'one'}}}}
    store.client = Receiver([packet])
    assert asyncio.run(store.products(1))[0]['variants'][0]['price'] == '19.00'
    row['variants']['pageInfo']['hasNextPage'] = True
    store.client = Receiver([packet])
    with pytest.raises(ActionError, match='catalogue_limit'):
        asyncio.run(store.products(1))
    store.client = Receiver([{'data': {'orders': {'nodes': [{'name': '#1234'}]}}}])
    assert asyncio.run(store.order('123')) is None
    with pytest.raises(ActionError, match='invalid_order_number'):
        asyncio.run(store.order('123 OR name:*'))


def test_woocommerce_read_only_currency_variants_and_exact_order_number():
    receiver = Receiver([[], [], {'code': 'INR'}, [{'id': 1, 'name': 'Book', 'type': 'variable',
        'description': '<b>Guide</b>'}], [{'attributes': [{'option': 'Blue'}], 'price': '10.50',
        'stock_status': 'instock', 'status': 'publish'}], [{'number': '1234'}]])
    store = Store('woocommerce', 'https://shop.example', {'key': 'ck_secret', 'secret': 'cs_secret'}, receiver)
    asyncio.run(store.test())
    product = asyncio.run(store.products(10))[0]
    assert product['description'] == 'Guide' and product['variants'][0]['price'] == '10.50'
    assert asyncio.run(store.order('123')) is None
    assert all(c[0] == 'GET' and 'cs_secret' not in c[1] for c in receiver.calls)
    assert 'billing' not in json.dumps(product)
