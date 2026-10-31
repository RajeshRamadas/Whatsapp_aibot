"""Admin-only endpoint demonstrating the durable worker pipeline."""
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from ..jobs.queue import enqueue
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/jobs', tags=['jobs'])


@router.post('/sample', status_code=202)
async def queue_sample(principal: Principal = Depends(require_role('admin'))):
    job_id = str(uuid4())
    try:
        queued_id = await enqueue('sample_job', tenant_id=principal.tenant.id, job_id=job_id, _job_id=job_id)
    except (OSError, ConnectionError, TimeoutError, RuntimeError) as exc:
        raise HTTPException(503, 'Background queue unavailable') from exc
    return {'job_id': queued_id, 'status': 'queued'}
