"""Admin-only website API, durable dispatch, plan enforcement and isolation."""
import asyncio
import hashlib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.db import ApiKey, Chunk, Plan, Session, Source, Website, now
from app.jobs import worker
from app.rag import retrieve
from app.services import website_crawl, website_dispatch
from tests.test_media import no_session_io as no_session_io

BASE = '/api/v1/websites'
BODY = {'url': 'https://example.com/knowledge#top', 'max_pages': 3, 'depth': 2, 'schedule': 'manual'}


def enable(tenant):
    with Session.begin() as db:
        db.get(Plan, 'starter').limits = {'ai_messages': 1000, 'crawl_pages': 5}
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Reader', key_prefix='fixture', scopes=['viewer'],
                     key_hash=hashlib.sha256(b'website-reader').hexdigest()))


def test_websites_api_permissions_validation_and_isolation(client, tenant):
    enable(tenant)
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other Website Workspace'}).json()
    foreign = {'X-Admin-Key': other['admin_key']}
    owner, viewer = tenant['headers'], {'Authorization': 'Bearer website-reader'}
    assert client.post(BASE, json=BODY, headers=owner).status_code == 202
    listing = client.get(BASE, headers=owner).json()
    assert listing['page_limit'] == 5 and listing['reserved_pages'] == 3 and listing['total'] == 1
    site = listing['items'][0]
    assert site['url'] == 'https://example.com/knowledge' and site['status'] == 'queued'
    identity = site['id']
    for method, path, body in [('get', BASE, None), ('post', BASE, BODY),
        ('post', f'{BASE}/{identity}/refresh', None), ('patch', f'{BASE}/{identity}', {'schedule': 'daily'}),
        ('delete', f'{BASE}/{identity}', None), ('get', f'{BASE}/{identity}/pages', None)]:
        for headers, expected in [({}, 401), (viewer, 403)]:
            assert client.request(method, path, json=body, headers=headers).status_code == expected
        response = client.request(method, path, json=body, headers=foreign)
        assert response.status_code == (200 if path == BASE and method == 'get' else
                                        202 if path == BASE else 404)
    assert client.get(BASE, headers={**owner, 'X-Workspace-Id': other['tenant_id']}).status_code == 404
    for bad in [{'url': 'http://example.com'}, {'url': 'https://127.0.0.1/'}, {'max_pages': 0},
                {'depth': 4}, {'schedule': 'hourly'}, {'tenant_id': other['tenant_id']}]:
        assert client.post(BASE, json={**BODY, **bad}, headers=owner).status_code == 422
    assert client.get(BASE + '?limit=0', headers=owner).status_code == 422
    assert client.post(BASE, json=BODY, headers=owner).status_code == 409
    assert client.post(BASE, json={**BODY, 'url': 'https://example.org/'}, headers=owner).status_code == 409
    for method, suffix, body in [('post', '/refresh', None), ('patch', '', {'schedule': 'weekly'}),
                                 ('delete', '', None), ('get', '/pages', None)]:
        assert client.request(method, BASE + '/invalid' + suffix, headers=owner, json=body).status_code == 422
    assert client.patch(f'{BASE}/{identity}', headers=owner, json={'schedule': 'hourly'}).status_code == 422
    with Session.begin() as db:
        site = db.get(Website, identity)
        site.status = 'ready'
        page_url = site.url + '?reference=' + 'a' * 300
        source = Source(tenant_id=tenant['tenant_id'], website_id=identity, url=page_url,
                        filename=page_url[:255], kind='url', file_path='web:', status='ready')
        db.add(source)
        db.flush()
        source_id = source.id
        db.add(Chunk(tenant_id=tenant['tenant_id'], source_id=source.id, position=0, content='Refunds',
                     embedding=[1, 0, 0]))
    assert client.get('/api/v1/sources', headers=owner).json() == []
    assert client.delete('/api/v1/sources/' + source_id, headers=owner).status_code == 404
    assert client.get(f'{BASE}/{identity}/pages', headers=owner).json() == [{'id': source_id, 'url': page_url}]
    assert retrieve(tenant['tenant_id'], 'refunds')[0].filename == page_url
    scheduled = client.patch(f'{BASE}/{identity}', headers=owner, json={'schedule': 'weekly'}).json()
    assert scheduled['schedule'] == 'weekly' and scheduled['next_run'] and scheduled['pages'] == 1
    assert client.post(f'{BASE}/{identity}/refresh', headers=owner).json()['pages'] == 1
    assert client.post(f'{BASE}/{identity}/refresh', headers=owner).status_code == 202
    with Session.begin() as db:
        db.get(Website, identity).status = 'running'
    assert client.post(f'{BASE}/{identity}/refresh', headers=owner).status_code == 409
    assert client.delete(f'{BASE}/{identity}', headers=owner).status_code == 204
    with Session() as db:
        assert db.get(Source, source_id) is None and db.scalar(select(Chunk.id)) is None
    assert client.get(f'{BASE}/{identity}/pages', headers=owner).status_code == 404


def test_dispatch_survives_queue_failure_and_stale_workers(client, tenant, monkeypatch, no_session_io):
    enable(tenant)
    identity = client.post(BASE, headers=tenant['headers'], json=BODY).json()['id']
    calls = []
    async def unavailable(name, **kwargs):
        assert no_session_io()
        calls.append(kwargs)
        raise ConnectionError('offline')
    monkeypatch.setattr(worker, 'enqueue', unavailable)
    asyncio.run(worker.dispatch_websites({}))
    assert len(calls) == 1 and calls[0]['tenant_id'] == tenant['tenant_id']
    assert website_dispatch.pending() == []  # Short dispatch lease prevents hot-loop retries.
    with Session.begin() as db:
        db.get(Website, identity).next_run = now() - timedelta(seconds=1)
        db.get(Website, identity).started_at = now() - timedelta(seconds=31)
    assert website_dispatch.pending()[0][:2] == (identity, tenant['tenant_id'])
    snapshot = website_crawl.claim(identity, tenant['tenant_id'])
    with Session.begin() as db:
        second = Website(tenant_id=tenant['tenant_id'], url='https://example.org/')
        db.add(second)
        db.flush()
        second_id = second.id
    assert website_crawl.claim(second_id, tenant['tenant_id']) is None  # Per-workspace concurrency.
    with Session.begin() as db:
        db.get(Website, identity).started_at = now() - timedelta(minutes=71)
    website_dispatch.pending()
    website_crawl.finish(snapshot)  # Superseded job cannot publish a successful result.
    with Session() as db:
        assert db.get(Website, identity).status == 'queued' and db.get(Website, identity).attempts == 1
    snapshot = website_crawl.claim(identity, tenant['tenant_id'])
    with Session.begin() as db:
        db.delete(db.get(Website, identity))
    website_crawl.finish(snapshot)
    assert website_crawl.claim(str(uuid4()), tenant['tenant_id']) is None
