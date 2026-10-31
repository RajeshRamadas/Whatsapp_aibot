"""arq worker entry point: python -m app.jobs.worker."""
import asyncio
from arq import run_worker
from arq.connections import RedisSettings

from .. import ingest
from ..config import settings
from ..db import AuditLog, Session, Source, Tenant


async def sample_job(ctx: dict, tenant_id: str, job_id: str) -> str:
    """Persist a tenant-scoped audit event to prove a queued job actually ran."""
    with Session.begin() as db:
        if not db.get(Tenant, tenant_id):
            raise ValueError('Unknown workspace')
        db.add(AuditLog(tenant_id=tenant_id, actor_type='system', action='jobs.sample.completed',
                        target=job_id, details={}))
    return job_id


async def ingest_source(ctx: dict, source_id: str, tenant_id: str) -> None:
    """Process an uploaded source; retry transient failures, fail on final attempt."""
    with Session() as db:
        source = db.get(Source, source_id)
        if not source or source.tenant_id != tenant_id:
            return  # Deleted source or stale/cross-tenant job.
        if source.status == 'ready':
            return
    try:
        await asyncio.to_thread(ingest.ingest, source_id)
    except Exception as exc:
        with Session.begin() as db:
            source = db.get(Source, source_id)
            if source and source.tenant_id == tenant_id:
                if isinstance(exc, ValueError) or ctx.get('job_try', 1) >= WorkerSettings.max_tries:
                    source.status, source.error = 'failed', str(exc)[:500]
                else:
                    source.status = 'queued'
        if not isinstance(exc, ValueError) and ctx.get('job_try', 1) < WorkerSettings.max_tries:
            raise


class WorkerSettings:
    functions = [sample_job, ingest_source]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 60
    max_tries = 3


if __name__ == '__main__':
    run_worker(WorkerSettings)
