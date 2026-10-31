"""Durable ingestion, worker restart, and atomic reprocessing tests."""
import asyncio

from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker
from sqlalchemy import func, select

from app.api import knowledge
from app.config import settings
from app.db import Chunk, Session, Source
from app.jobs.queue import enqueue
from app.jobs.worker import ingest_source


def test_ingest_waits_for_worker(client, tenant, monkeypatch):
    monkeypatch.setattr(knowledge, 'enqueue', enqueue)
    result = client.post('/api/v1/sources', headers=tenant['headers'],
                         files={'file': ('refund.txt', b'Refunds accepted within 30 days.', 'text/plain')})
    assert result.status_code == 202, result.text
    source_id = result.json()['source_id']
    with Session() as db:
        assert db.get(Source, source_id).status == 'queued'
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.source_id == source_id)) == 0

    async def run_worker():
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            worker = Worker(functions=[ingest_source], redis_pool=redis, burst=True,
                            poll_delay=0.1, max_jobs=1)
            await worker.async_run()
        finally:
            await redis.aclose()

    asyncio.run(run_worker())
    with Session() as db:
        assert db.get(Source, source_id).status == 'ready'
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.source_id == source_id)) == 1


def test_reprocessing_replaces_chunks(client, tenant):
    result = client.post('/api/v1/sources', headers=tenant['headers'],
                         files={'file': ('hours.txt', b'Hours are 9 to 5.', 'text/plain')})
    assert result.status_code == 202
    source_id = result.json()['source_id']
    with Session.begin() as db:
        source = db.get(Source, source_id)
        source.status = 'queued'
    asyncio.run(ingest_source({'job_try': 1}, source_id, tenant['tenant_id']))
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.source_id == source_id)) == 1
