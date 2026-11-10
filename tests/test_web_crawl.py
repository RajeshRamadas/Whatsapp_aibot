"""Offline crawl contracts: public-address pinning, robots, budgets and index refreshes."""
import asyncio
import socket
import ssl
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.db import Chunk, Plan, Session, Source, Website, now
from app.services import web_fetch as fetch
from app.services import website_crawl as crawl
from app.services.web_content import extract
from tests.conftest import FakeEmbedder
from tests.test_media import no_session_io as no_session_io


@pytest.mark.parametrize('url', ['http://example.com/', 'https://localhost/', 'https://127.0.0.1/',
    'https://[::1]/', 'https://169.254.169.254/', 'https://[::ffff:127.0.0.1]/',
    'https://user:secret@example.com/', 'https://example.com:8080/', 'https://2130706433/',
    'https://[64:ff9b::7f00:1]/', 'https://[64:ff9b:1::a00:1]/', 'https://168.63.129.16/'])
def test_rejects_nonpublic_urls(url):
    with pytest.raises(fetch.FetchError):
        fetch.canonical(url)


def test_dns_validation_and_tls_identity(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))])
    with pytest.raises(fetch.FetchError):
        fetch.resolve('rebinding.example.com')
    context = fetch.PinnedTLS('example.com')
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    seen = []
    monkeypatch.setattr(ssl.SSLContext, 'wrap_socket', lambda *args, **kwargs: seen.append(kwargs['server_hostname']))
    monkeypatch.setattr(ssl.SSLContext, 'wrap_bio', lambda *args, **kwargs: seen.append(kwargs['server_hostname']))
    context.wrap_socket(None, server_hostname='93.184.216.34')
    context.wrap_bio(None, None, server_hostname='93.184.216.34')
    assert seen == ['example.com', 'example.com']


def test_fetch_pins_ip_and_blocks_redirect_before_request(monkeypatch):
    requested = []
    monkeypatch.setattr(fetch, 'resolve', lambda host: '93.184.216.34')
    monkeypatch.setattr(fetch, 'proxy_bypass', lambda host: False)
    monkeypatch.setattr(fetch, 'getproxies', lambda: {'https': 'http://proxy.example.com:8080'})
    class Client:
        def __init__(self, **kwargs):
            assert not kwargs['trust_env']
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        @asynccontextmanager
        async def stream(self, method, url, **kwargs):
            requested.append(str(url))
            assert url.host == '93.184.216.34' and kwargs['headers']['Host'] == 'example.com'
            yield SimpleNamespace(status_code=302, headers={'location': '/private'})
    @asynccontextmanager
    async def permit(*args):
        yield
    monkeypatch.setattr(fetch.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(fetch.Fetcher, 'permit', permit)
    async def check():
        client = fetch.Fetcher()
        try:
            with pytest.raises(fetch.FetchError, match='disallowed'):
                await client.get('https://example.com/', allowed=lambda url: not url.endswith('/private'))
        finally:
            await client.close()
    asyncio.run(check())
    assert requested == ['https://93.184.216.34/']


def test_main_content_headings_and_safe_link_discovery():
    blocks, links = extract('<nav>Menu <a href="/refunds">Refunds</a></nav><main><h1>Policy</h1>'
        '<h2>Refunds</h2><p>Within 30 days.</p><script>private script</script></main><footer>Noise</footer>'
        '<a href="https://127.0.0.1/">private</a>', 'https://example.com/', 'example.com')
    assert [(block.heading, block.text) for block in blocks] == [('Policy > Refunds', 'Within 30 days.')]
    assert links == ['https://example.com/refunds']


def test_rate_limit_is_shared_across_fetchers():
    async def check():
        first, second = fetch.Fetcher(), fetch.Fetcher()
        host, stamps = str(uuid4()) + '.example.com', []
        async def request(client):
            async with client.permit(host):
                stamps.append(time.monotonic())
        try:
            await asyncio.gather(request(first), request(second))
            assert stamps[1] - stamps[0] >= .95
        finally:
            await first.close()
            await second.close()
    asyncio.run(check())


def test_crawl_robots_checksum_refresh_and_404(client, tenant, monkeypatch, no_session_io):
    with Session.begin() as db:
        db.get(Plan, 'starter').limits = {'ai_messages': 1000, 'crawl_pages': 5}
        site = Website(tenant_id=tenant['tenant_id'], url='https://example.com/', max_pages=3, depth=2)
        db.add(site)
        db.flush()
        identity = site.id
    requests, embeddings = [], []
    def embed(texts, **kwargs):
        assert no_session_io()
        embeddings.extend(texts)
        return FakeEmbedder().embed(texts)
    monkeypatch.setattr(crawl.llm, 'embedder', lambda: SimpleNamespace(embed=embed))
    responses = {'/': '<main><h1>Policy</h1><p>Refund terms.</p><a href="/refunds">Refunds</a>'
                 '<a href="/private">Private</a></main>', '/refunds': '<h1>Refunds</h1><p>Within 30 days.</p>'}
    missing = set()
    class FakeFetcher:
        interval = 1
        async def get(self, url, **kwargs):
            assert no_session_io()
            path = httpx.URL(url).path
            requests.append(path)
            text = 'User-agent: *\nDisallow: /private*' if path == '/robots.txt' else responses[path]
            return url, 404 if path in missing else 200, text, {'content-type': 'text/html'}
        async def close(self):
            pass
    monkeypatch.setattr(crawl, 'Fetcher', FakeFetcher)
    def run():
        with Session.begin() as db:
            db.get(Website, identity).status = 'queued'
        asyncio.run(crawl.crawl(identity, tenant['tenant_id']))
    run()
    with Session() as db:
        assert db.get(Website, identity).status == 'ready'
        original = {row.id for row in db.scalars(select(Chunk))}
        assert len(original) == 2
    assert '/private' not in requests
    embedding_count = len(embeddings)
    responses['/'] += '<footer>Changed footer only</footer>'
    run()
    with Session() as db:
        assert {row.id for row in db.scalars(select(Chunk))} == original
    assert len(embeddings) == embedding_count
    responses['/refunds'] = '<h1>Refunds</h1><p>Within 60 days.</p>'
    run()
    with Session() as db:
        assert db.scalar(select(Chunk).where(Chunk.content == 'Within 60 days.'))
    missing.add('/refunds')
    run()
    with Session() as db:
        assert db.scalar(select(Source).where(Source.url.endswith('/refunds'))) is None
    before = len(requests)
    asyncio.run(crawl.crawl(identity, 'foreign-workspace'))
    assert len(requests) == before


def test_budget_retry_exhaustion_and_weekly_schedule(client, tenant):
    with Session.begin() as db:
        site = Website(tenant_id=tenant['tenant_id'], url='https://example.com/', schedule='weekly')
        db.add(site)
        db.flush()
        identity = site.id
    assert crawl.claim(identity, tenant['tenant_id']) is None  # Starter has no crawler entitlement.
    with Session.begin() as db:
        db.get(Plan, 'starter').limits = {'crawl_pages': 1}
        db.get(Website, identity).status = 'queued'
    for attempt in range(5):
        with Session.begin() as db:
            db.get(Website, identity).next_run = now()
        snapshot = crawl.claim(identity, tenant['tenant_id'])
        crawl.finish(snapshot, fetch.FetchError('Busy', 90))
        with Session() as db:
            site = db.get(Website, identity)
            assert site.status == ('queued' if attempt < 4 else 'failed')
            if attempt < 4:
                assert (site.next_run - now()).total_seconds() >= 89
        if attempt < 4:
            assert crawl.claim(identity, tenant['tenant_id']) is None
    with Session.begin() as db:
        db.get(Website, identity).status = 'queued'
        db.get(Website, identity).next_run = now()
    crawl.finish(crawl.claim(identity, tenant['tenant_id']))
    with Session() as db:
        site = db.get(Website, identity)
        assert site.status == 'ready' and site.last_success
        assert 6.99 < (site.next_run - now()).total_seconds() / 86400 <= 7
