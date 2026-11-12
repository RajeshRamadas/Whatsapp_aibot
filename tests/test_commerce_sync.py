"""Full snapshots, retry/crash recovery, disconnect fencing and cited customer tools."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import llm, rag
from app.db import Chunk, Conversation, Session, Source, Tenant, engine, now, uid
from app.integrations.commerce import Provider
from app.integrations.http_actions.transport import ActionError
from app.models.commerce import Store
from app.services import commerce, commerce_chat, commerce_sync, grounding
from tests.test_commerce import BASE, store  # noqa: F401

PRODUCT = {'id': 'product-1', 'title': 'Shipping box', 'description': '<b>Recycled box</b>',
           'currency': 'INR', 'variants': [{'title': 'Small', 'price': '50.00', 'inventoryQuantity': 3}]}


@pytest.fixture
def catalogue(store, monkeypatch):  # noqa: F811
    products = [PRODUCT]
    async def fetch(self):
        assert engine.pool.checkedout() == 0
        return list(products)
    monkeypatch.setattr(Provider, 'products', fetch)
    return products


def test_atomic_catalogue_replacement_retry_and_disconnect(client, tenant, store, catalogue, monkeypatch):  # noqa: F811
    tid, headers = tenant['tenant_id'], tenant['headers']
    assert client.post(f'{BASE}/{store}/sync', headers=headers).status_code == 202
    assert commerce_sync.process_one()
    with Session() as db:
        row = db.scalar(select(Store).where(Store.tenant_id == tid))
        source_id = row.source_id
        chunk = db.scalar(select(Chunk).where(Chunk.tenant_id == tid, Chunk.source_id == source_id))
        assert row.product_count == 1 and row.last_error is None
        assert 'Recycled box' in chunk.content and '<b>' not in chunk.content and 'snapshot_at' in chunk.content
        assert '50.00' in chunk.content and 'INR' in chunk.content
    assert source_id not in [s['id'] for s in client.get('/api/v1/sources', headers=headers).json()]
    assert client.delete('/api/v1/sources/' + source_id, headers=headers).status_code == 404
    assert not commerce_sync.process_one()  # Not due for another day.
    async def fail(self):
        raise ActionError('timeout')
    monkeypatch.setattr(Provider, 'products', fail)
    commerce_sync.queue(tid, store)
    assert commerce_sync.process_one()
    with Session() as db:
        assert db.scalar(select(Chunk.id).where(Chunk.tenant_id == tid, Chunk.source_id == source_id))
        assert db.scalar(select(Store.last_error).where(Store.tenant_id == tid)) == 'timeout'
    async def empty(self):
        return []
    monkeypatch.setattr(Provider, 'products', empty)
    with Session.begin() as db:
        row = db.scalar(select(Store).where(Store.tenant_id == tid))
        row.lease, row.next_run = uid(), now() - timedelta(minutes=1)  # Crashed worker lease.
    assert commerce_sync.process_one()
    with Session() as db:
        assert db.scalar(select(Chunk.id).where(Chunk.tenant_id == tid, Chunk.source_id == source_id)) is None
    def unsafe_embed(texts):
        assert engine.pool.checkedout() == 0
        commerce.disconnect(tid, store)
        return [[1, 0, 0] for _ in texts]
    async def fetch(self):
        return [PRODUCT]
    monkeypatch.setattr(Provider, 'products', fetch)
    monkeypatch.setattr(llm, 'embedder', lambda: type('Embed', (), {'embed': staticmethod(unsafe_embed)})())
    commerce_sync.queue(tid, store)
    assert commerce_sync.process_one()
    with Session() as db:
        assert db.scalar(select(Source.id).where(Source.tenant_id == tid, Source.id == source_id)) is None


def test_sync_auth_isolation_and_validation(client, tenant, store):  # noqa: F811
    assert client.post(f'{BASE}/{store}/sync').status_code == 401
    assert client.post(BASE + '/invalid/sync', headers=tenant['headers']).status_code == 422
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other store'}).json()
    assert client.post(f'{BASE}/{store}/sync', headers={'X-Admin-Key': other['admin_key']}).status_code == 404
    from app.db import ApiKey
    from app.services.auth import hash_token
    key = uid()
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], key_hash=hash_token(key), key_prefix=key[:8],
                      name='Agent', scopes=['agent']))
    assert client.post(f'{BASE}/{store}/sync', headers={'X-Admin-Key': key}).status_code == 403
    with Session.begin() as db:
        row = db.scalar(select(Store).where(Store.tenant_id == tenant['tenant_id']))
        row.status = 'needs_test'
    assert client.post(f'{BASE}/{store}/sync', headers=tenant['headers']).status_code == 409


def test_live_order_grounding_and_web_privacy(client, tenant, store, monkeypatch, chats):  # noqa: F811
    tid = tenant['tenant_id']
    with Session.begin() as db:
        wa, web = uid(), uid()
        db.add_all([Conversation(id=wa, tenant_id=tid, channel='whatsapp', external_user='919999000001'),
                    Conversation(id=web, tenant_id=tid, channel='web', external_user='919999000001')])
        workspace = db.get(Tenant, tid)
    async def order(self, number):
        return {'phones': ['+919999000001'], 'number': number, 'status': 'paid', 'items': [], 'tracking': []}
    monkeypatch.setattr(Provider, 'order', order)
    fake = chats['openai']
    fake.tool_turns = [lambda results, tools: ('', [('lookup_order', {'order_number': '1001'})]),
                       lambda results, tools: ('Order 1001 is paid ['
                           + str(results[0]['result']['source']['number']) + ']', [])]
    result = rag.answer(workspace, wa, 'Status of order 1001?', whatsapp=True)
    assert not result.uncertain and result.sources[0]['filename'] == 'Order status'
    assert 'phones' not in fake.verifications[-1]['messages'][0]['content']
    assert commerce_chat.tools(workspace, web, evidence=grounding.Evidence()) == []
    fake.grounding_verdict = {'verdict': 'unsupported', 'citations': []}
    assert rag.answer(workspace, wa, 'Status of order 1001?', whatsapp=True).uncertain


def test_provider_product_and_order_payloads():
    import asyncio
    from types import SimpleNamespace
    class Woo:
        async def request(self, method, url, headers, **kwargs):
            assert method == 'GET' and 'consumer_secret' not in url and 'Basic ' in headers['Authorization']
            if 'woocommerce_currency' in url:
                return 200, {'value': 'INR'}
            if '/orders?' in url:
                return 200, [{'number': '1001', 'status': 'completed', 'billing': {'phone': '+919999000001'},
                              'line_items': [{'name': 'Box', 'quantity': 2}]}]
            return 200, [{'id': 1, 'name': 'Box', 'description': 'Shipping', 'type': 'simple',
                          'price': '50.00', 'stock_status': 'instock'}]
    row = SimpleNamespace(provider='woocommerce', url='https://store.example')
    provider = Provider(row, {'token': 'consumer-secret', 'consumer_key': 'ck_' + 'a' * 40}, Woo())
    products = asyncio.run(provider.products())
    assert products[0]['currency'] == 'INR' and products[0]['variants'][0]['price'] == '50.00'
    assert asyncio.run(provider.order('1001'))['items'] == [{'title': 'Box', 'quantity': 2}]
    assert asyncio.run(provider.order('1002')) is None
    class Shopify:
        async def request(self, method, url, headers, **kwargs):
            query = kwargs['body']['query']
            assert method == 'POST' and query.lstrip().startswith('query ') and 'mutation' not in query
            if 'query Order(' in query:
                return 200, {'data': {'orders': {'nodes': [{'name': '#1001', 'phone': '+919999000001',
                    'displayFinancialStatus': 'PAID', 'displayFulfillmentStatus': 'FULFILLED',
                    'lineItems': {'nodes': [{'title': 'Box', 'quantity': 2}], 'pageInfo': {'hasNextPage': False}},
                    'fulfillments': [{'status': 'SUCCESS', 'trackingInfo': [{'company': 'Courier', 'number': 'abc',
                                                                          'url': 'https://track.example/abc'}]}]}]}}}
            return 200, {'data': {'shop': {'currencyCode': 'INR'}, 'products': {
                'nodes': [{'id': 'gid://shopify/Product/1', 'title': 'Box', 'description': 'Shipping',
                    'variants': {'nodes': [{'title': 'Small', 'price': '50.00', 'inventoryQuantity': 3}],
                                 'pageInfo': {'hasNextPage': False}}}],
                'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}
    provider = Provider(SimpleNamespace(provider='shopify', url='https://demo.myshopify.com'),
                        {'token': 'private-secret', 'consumer_key': ''}, Shopify())
    assert asyncio.run(provider.products())[0]['variants'][0]['inventoryQuantity'] == 3
    assert asyncio.run(provider.order('1001'))['tracking'][0]['trackingInfo'][0]['number'] == 'abc'
