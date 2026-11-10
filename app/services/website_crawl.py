"""Tenant-scoped crawl jobs and atomic publication of changed public pages."""
import asyncio
import hashlib
import heapq
import json
import random
from datetime import timedelta
from urllib.parse import urlsplit

from protego import Protego
from sqlalchemy import delete, func, select

from .. import llm
from ..config import settings
from ..db import Chunk, Plan, Session, Source, Tenant, Website, now, uid
from ..ingest import EMBED_BATCH, chunk_blocks, embedding_text
from .billing import entitled
from .events import publish_sync
from .usage import meter
from .web_content import extract
from .web_fetch import AGENT, Fetcher, FetchError


class StaleRun(Exception):
    pass


def budget(db, tenant):
    plan = db.get(Plan, tenant.plan_id)
    value = (plan.limits if plan else {}).get('crawl_pages', 0)
    return max(0, min(value, settings.crawl_page_cap)) if type(value) is int else 0


def reserved(db, tenant_id):
    return db.scalar(select(func.coalesce(func.sum(Website.max_pages), 0)).where(Website.tenant_id == tenant_id))


def scoped(db, snapshot, *, lock=False):
    query = select(Website).where(Website.id == snapshot.id, Website.tenant_id == snapshot.tenant_id,
                                 Website.generation == snapshot.generation, Website.status == 'running')
    site = db.scalar(query.with_for_update() if lock else query)
    if site is None:
        raise StaleRun()
    tenant = db.get(Tenant, snapshot.tenant_id)
    if (not tenant or tenant.suspended or not entitled(db, tenant, now()) or
            reserved(db, tenant.id) > budget(db, tenant)):
        raise FetchError('Crawling is paused by the workspace plan or account status.')
    return site


def claim(identity, tenant_id):
    with Session.begin() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        site = db.scalar(select(Website).where(Website.id == identity, Website.tenant_id == tenant_id)
                         .with_for_update())
        if not site or site.status != 'queued' or (site.next_run and site.next_run > now()):
            return None
        if (not tenant or tenant.suspended or not entitled(db, tenant, now()) or
                not 1 <= site.max_pages <= 50 or not 0 <= site.depth <= 3 or
                reserved(db, tenant_id) > budget(db, tenant)):
            site.status, site.error, site.next_run = 'failed', 'Website page budgets exceed the current plan.', None
            return None
        if db.scalar(select(Website.id).where(Website.tenant_id == tenant_id,
                Website.status == 'running', Website.id != identity).limit(1)):
            return None  # One active crawl per workspace; its other queued sites remain durable.
        site.status, site.started_at, site.generation = 'running', now(), uid()
        return site


def remove(snapshot, url, *aliases):
    with Session.begin() as db:
        scoped(db, snapshot, lock=True)
        db.execute(delete(Source).where(Source.tenant_id == snapshot.tenant_id,
                                        Source.website_id == snapshot.id, Source.url.in_((url, *aliases))))


def publish(snapshot, url, requested, depth, blocks):
    digest = hashlib.sha256(json.dumps([(b.heading, b.text, b.row) for b in blocks]).encode()).hexdigest()
    with Session() as db:
        scoped(db, snapshot)
        previous = db.scalar(select(Source).where(Source.tenant_id == snapshot.tenant_id,
                             Source.website_id == snapshot.id, Source.url == url))
        changed = previous is None or previous.checksum != digest
    pieces = chunk_blocks(blocks) if changed else []
    vectors = []
    if changed:
        with meter(snapshot.tenant_id):
            embedder = llm.embedder()
            for start in range(0, len(pieces), EMBED_BATCH):
                vectors.extend(embedder.embed([embedding_text(url[:255], piece)
                                               for piece in pieces[start:start + EMBED_BATCH]]))
    with Session.begin() as db:
        scoped(db, snapshot, lock=True)
        if requested != url:
            db.execute(delete(Source).where(Source.tenant_id == snapshot.tenant_id,
                Source.website_id == snapshot.id, Source.url == requested))
        source = db.scalar(select(Source).where(Source.tenant_id == snapshot.tenant_id,
                           Source.website_id == snapshot.id, Source.url == url))
        if source is None:
            count = db.scalar(select(func.count()).select_from(Source).where(
                Source.tenant_id == snapshot.tenant_id, Source.website_id == snapshot.id))
            if count >= snapshot.max_pages:
                return
            source = Source(tenant_id=snapshot.tenant_id, website_id=snapshot.id, url=url,
                            filename=url[:255], kind='url', file_path='web:', status='ready')
            db.add(source)
            db.flush()
        source.crawl_depth = depth
        if changed:
            db.execute(delete(Chunk).where(Chunk.tenant_id == snapshot.tenant_id, Chunk.source_id == source.id))
            db.add_all(Chunk(tenant_id=snapshot.tenant_id, source_id=source.id, position=i,
                content=piece.content, heading=piece.heading, page=None, embedding=vector)
                for i, (piece, vector) in enumerate(zip(pieces, vectors, strict=True)))
            source.checksum = digest


def finish(snapshot, error=None):
    with Session.begin() as db:
        site = db.scalar(select(Website).where(Website.id == snapshot.id, Website.tenant_id == snapshot.tenant_id,
                         Website.generation == snapshot.generation, Website.status == 'running').with_for_update())
        if not site:
            return
        site.error = str(error) if error else None
        if error and error.retry and site.attempts < 4:
            site.attempts += 1
            site.status = 'queued'
            delay = max(error.retry, 30 * 2 ** (site.attempts - 1)) + random.uniform(0, 5)
            try:
                site.next_run = now() + timedelta(seconds=delay)
            except OverflowError:
                site.status, site.next_run = 'failed', None
        else:
            site.status = 'failed' if error else 'ready'
            site.attempts = 0
            if not error:
                site.last_success = now()
            days = {'daily': 1, 'weekly': 7}.get(site.schedule)
            site.next_run = now() + timedelta(days=days) if days else None


async def crawl(identity, tenant_id):
    snapshot = await asyncio.to_thread(claim, identity, tenant_id)
    if snapshot is None:
        return
    fetcher = Fetcher()
    try:
        host = urlsplit(snapshot.url).hostname
        _, status, body, _ = await fetcher.get(f'https://{urlsplit(snapshot.url).netloc}/robots.txt')
        if status not in (200, 404, 410):
            raise FetchError('The website did not allow its crawling rules to be read.')
        rules = Protego.parse(body if status == 200 else '')
        delay = max(1, rules.crawl_delay(AGENT) or 1)
        if delay > 30:
            raise FetchError('The website requires a crawl delay longer than supported. Use another public source.')
        fetcher.interval = delay
        if delay > 1:
            await fetcher.pause(host, delay)
        with Session() as db:
            scoped(db, snapshot)
            known = db.execute(select(Source.url, Source.crawl_depth).where(
                Source.tenant_id == tenant_id, Source.website_id == identity)).all()
        for url, _ in known:
            if not rules.can_fetch(url, AGENT):
                await asyncio.to_thread(remove, snapshot, url)
        queue = [(-1, 0, snapshot.url), *[(0, depth or 0, url) for url, depth in known]]
        heapq.heapify(queue)
        seen, successful, attempts = set(), 0, 0
        while queue and successful < snapshot.max_pages and attempts < snapshot.max_pages * 3 + 10:
            _, depth, url = heapq.heappop(queue)
            if url in seen:
                continue
            seen.add(url)
            with Session() as db:
                scoped(db, snapshot)
            if not rules.can_fetch(url, AGENT):
                await asyncio.to_thread(remove, snapshot, url)
                if url == snapshot.url:
                    raise FetchError('The starting page is disallowed by robots.txt.')
                continue
            attempts += 1
            final, status, body, headers = await fetcher.get(url, allowed=lambda target: rules.can_fetch(target, AGENT))
            if status in (401, 403, 404, 410):
                await asyncio.to_thread(remove, snapshot, url, final)
                if url == snapshot.url:
                    raise FetchError('The starting page is missing or requires private access.')
                continue
            if status != 200:
                raise FetchError('A website page could not be read.')
            if not rules.can_fetch(final, AGENT):
                await asyncio.to_thread(remove, snapshot, url)
                raise FetchError('A redirect reached a page disallowed by robots.txt.')
            if final != url and final in seen:
                await asyncio.to_thread(remove, snapshot, url)
                continue
            seen.add(final)
            mime = headers.get('content-type', '').split(';')[0].lower()
            if mime not in ('text/html', 'application/xhtml+xml', 'text/plain'):
                await asyncio.to_thread(remove, snapshot, url, final)
                if url == snapshot.url:
                    raise FetchError('The starting page must provide HTML or plain text.')
                continue
            blocks, links = extract(body, final, host)
            if not blocks or 'noindex' in headers.get('x-robots-tag', '').lower():
                await asyncio.to_thread(remove, snapshot, url, final)
                if url == snapshot.url:
                    raise FetchError('The starting page has no indexable public text.')
                continue
            await asyncio.to_thread(publish, snapshot, final, url, depth, blocks)
            successful += 1
            if depth < snapshot.depth:
                for link in links[:500]:
                    if len(queue) < snapshot.max_pages * 20 and link not in seen:
                        heapq.heappush(queue, (1, depth + 1, link))
        await asyncio.to_thread(finish, snapshot)
    except StaleRun:
        pass
    except FetchError as error:
        await asyncio.to_thread(finish, snapshot, error)
    except Exception:
        await asyncio.to_thread(finish, snapshot, FetchError('Crawling was interrupted. It will retry later.', 30))
    finally:
        await fetcher.close()
        await asyncio.to_thread(publish_sync, tenant_id, 'source.updated', {'website_id': identity})
