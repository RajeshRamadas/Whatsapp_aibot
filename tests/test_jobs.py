"""Real Redis queue/worker integration and endpoint authorization tests."""
import asyncio

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker
from sqlalchemy import select

from app.config import settings
from app.db import AuditLog, Session
from app.jobs.worker import sample_job


@pytest.fixture
def inline_job():
    """Execute a registered job inline without Redis for fast unit tests."""
    def run(tenant_id: str, job_id: str):
        return asyncio.run(sample_job({}, tenant_id=tenant_id, job_id=job_id))
    return run


def test_sample_job_inline(tenant, inline_job):
    assert inline_job(tenant['tenant_id'], 'inline-123') == 'inline-123'
    with Session() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.tenant_id == tenant['tenant_id'],
            AuditLog.action == 'jobs.sample.completed', AuditLog.target == 'inline-123'))


def test_sample_job_endpoint_auth_and_worker(tenant, client):
    assert client.post('/api/v1/jobs/sample').status_code == 401
    response = client.post('/api/v1/jobs/sample', headers=tenant['headers'])
    assert response.status_code == 202, response.text
    job_id = response.json()['job_id']

    async def run_worker_once():
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            worker = Worker(functions=[sample_job], redis_pool=redis, burst=True,
                            poll_delay=0.1, max_jobs=1)
            await worker.async_run()
        finally:
            await redis.aclose()

    asyncio.run(run_worker_once())
    with Session() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.tenant_id == tenant['tenant_id'],
            AuditLog.action == 'jobs.sample.completed', AuditLog.target == job_id))
        assert row is not None
