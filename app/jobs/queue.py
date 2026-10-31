"""Enqueue named arq jobs using a shared Redis connection pool."""
from arq import create_pool
from arq.connections import RedisSettings

from ..config import settings

_pool = None


async def enqueue(name: str, **kwargs) -> str:
    """Return the job ID after Redis accepts the job; propagate connection failures."""
    global _pool
    if name not in {'sample_job', 'ingest_source'}:
        raise ValueError('Unknown job')
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    job = await _pool.enqueue_job(name, **kwargs)
    if job is None:
        raise RuntimeError('Job was not accepted by Redis')
    return job.job_id


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
