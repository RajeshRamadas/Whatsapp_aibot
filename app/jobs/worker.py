"""arq worker entry point: python -m app.jobs.worker."""
from arq import run_worker
from arq.connections import RedisSettings

from ..config import settings
from ..db import AuditLog, Session, Tenant


async def sample_job(ctx: dict, tenant_id: str, job_id: str) -> str:
    """Persist a tenant-scoped audit event to prove a queued job actually ran."""
    with Session.begin() as db:
        if not db.get(Tenant, tenant_id):
            raise ValueError('Unknown workspace')
        db.add(AuditLog(tenant_id=tenant_id, actor_type='system', action='jobs.sample.completed',
                        target=job_id, details={}))
    return job_id


class WorkerSettings:
    functions = [sample_job]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 60
    max_tries = 3


if __name__ == '__main__':
    run_worker(WorkerSettings)
