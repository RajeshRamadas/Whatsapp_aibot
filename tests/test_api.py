import time

from sqlalchemy import func, select

from app import rag
from app.db import Chunk, Session


def upload(client, tenant, name, text):
    r = client.post('/api/v1/sources', headers=tenant['headers'], files={'file': (name, text.encode(), 'text/plain')})
    assert r.status_code == 202, r.text
    return r.json()['source_id']


def test_tenant_requires_bootstrap_token(client):
    assert client.post('/api/v1/tenants', json={'name': 'X store'}).status_code == 401
    r = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'wrong-é'.encode()}, json={'name': 'X store'})
    assert r.status_code == 401


def test_admin_key_required(client, tenant):
    assert client.get('/api/v1/sources').status_code == 401
    assert client.get('/api/v1/sources', headers={'X-Admin-Key': 'nope'}).status_code == 401


def test_upload_chat_and_relevant_sources(client, tenant, chats):
    upload(client, tenant, 'refunds.txt', 'Our refund policy: refund within 30 days.')
    upload(client, tenant, 'shipping.md', 'Shipping takes 3 days. Shipping is free.')
    assert {s['status'] for s in client.get('/api/v1/sources', headers=tenant['headers']).json()} == {'ready'}

    r = client.post(f"/api/v1/chat/{tenant['public_key']}", json={'message': 'How do refunds work?'})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['answer'] == 'openai:gpt-4.1-mini: How do refunds work?'
    assert [s['filename'] for s in body['sources']] == ['refunds.txt']  # shipping is too far away
    assert 'refund within 30 days' in chats['openai'].calls[0]['system']

    r = client.post(f"/api/v1/chat/{tenant['public_key']}",
                    json={'message': 'And shipping?', 'conversation_id': body['conversation_id']})
    assert r.status_code == 200
    turns = chats['openai'].calls[1]['messages']
    assert [t['role'] for t in turns] == ['user', 'assistant', 'user']
    assert turns[0]['content'] == 'How do refunds work?'


def test_failed_answer_leaves_no_conversation(client, tenant, chats, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('provider down')
    monkeypatch.setattr(chats['openai'], 'complete', boom)
    client_noraise = type(client)(client.app, raise_server_exceptions=False)
    r = client_noraise.post(f"/api/v1/chat/{tenant['public_key']}", json={'message': 'hello'})
    assert r.status_code == 500
    r = client.get('/api/v1/conversations', headers=tenant['headers'])
    assert r.json() == []


def test_origin_checks(client, tenant):
    url = f"/api/v1/chat/{tenant['public_key']}"
    assert client.post(url, json={'message': 'hi'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    r = client.options(url, headers={'Origin': 'https://shop.example', 'Access-Control-Request-Method': 'POST'})
    assert r.status_code == 204 and r.headers['access-control-allow-origin'] == 'https://shop.example'


def test_rate_limit(client, tenant, monkeypatch):
    monkeypatch.setattr(rag.chat_limiter, 'limit', 2)
    url = f"/api/v1/chat/{tenant['public_key']}"
    codes = [client.post(url, json={'message': 'hi'}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_per_tenant_provider(client, tenant, chats):
    r = client.patch('/api/v1/tenant', headers=tenant['headers'], json={'chat_provider': 'anthropic'})
    assert r.status_code == 200 and r.json()['chat_model'] == 'claude-opus-5-5'
    r = client.post(f"/api/v1/chat/{tenant['public_key']}", json={'message': 'hi'})
    assert r.json()['answer'].startswith('anthropic:claude-opus-5-5')

    r = client.patch('/api/v1/tenant', headers=tenant['headers'],
                     json={'chat_provider': 'gemini', 'chat_model': 'gemini-2.5-pro'})
    assert r.json()['chat_model'] == 'gemini-2.5-pro'
    assert client.post(f"/api/v1/chat/{tenant['public_key']}", json={'message': 'hi'}).json()['answer'] \
        .startswith('gemini:gemini-2.5-pro')

    assert client.patch('/api/v1/tenant', headers=tenant['headers'],
                        json={'chat_provider': 'llama'}).status_code == 422
    r = client.patch('/api/v1/tenant', headers=tenant['headers'], json={'chat_provider': None})
    assert r.json()['chat_provider'] == 'openai'


def test_delete_source_removes_chunks(client, tenant):
    sid = upload(client, tenant, 'hours.txt', 'Opening hours are 9 to 5.')
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Chunk)) == 1
    assert client.delete(f'/api/v1/sources/{sid}', headers=tenant['headers']).status_code == 204
    assert client.get('/api/v1/sources', headers=tenant['headers']).json() == []
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0


def test_failed_ingest_is_reported(client, tenant):
    upload(client, tenant, 'empty.txt', '   ')
    [src] = client.get('/api/v1/sources', headers=tenant['headers']).json()
    assert src['status'] == 'failed' and 'No extractable text' in src['error']


def test_rate_limiter_window():
    limiter = rag.RateLimiter(1, window_seconds=0.05)
    assert limiter.allow('k') and not limiter.allow('k')
    time.sleep(0.06)
    assert limiter.allow('k')
