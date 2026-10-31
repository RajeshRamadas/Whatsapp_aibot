# ruff: noqa: F401
import hashlib
import json
import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import evaluate, ingest, llm, rag, whatsapp
from ..config import settings
from ..db import Conversation, EvalCase, Message, Session, Source, Tenant, uid
from ..services.audit import audit
from ..services.storage import LocalStorage, storage
from .principal import Principal, require_role, tenant_for_role
from .shared import (
    UPLOADS,
    WIDGET,
    AgentReply,
    ChatInput,
    CreateTenant,
    EvalCasesInput,
    UpdateTenant,
    admin_tenant,
    eval_view,
    origins,
    tenant_view,
)

log = logging.getLogger(__name__)
router = APIRouter()

@router.post('/api/v1/sources', status_code=202)
async def upload(background: BackgroundTasks, file: UploadFile = File(...),
                 tenant: Tenant = Depends(tenant_for_role('admin'))):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ingest.SUPPORTED_SUFFIXES:
        raise HTTPException(415, 'Unsupported file type')
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, 'File too large')
    source_id = uid()
    key = f'{tenant.id}/{source_id}{suffix}'
    storage().put(key, content)
    with Session.begin() as db:
        db.add(Source(id=source_id, tenant_id=tenant.id, filename=Path(file.filename or 'file').name[:255],
                      kind=suffix[1:], file_path='storage:' + key, status='queued'))
    background.add_task(ingest.ingest, source_id)  # Dev only: use a durable queue in production.
    return {'source_id': source_id, 'status': 'queued'}


@router.get('/api/v1/sources')
def list_sources(tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session() as db:
        sources = db.scalars(select(Source).where(Source.tenant_id == tenant.id).order_by(Source.created_at))
        return [{'id': s.id, 'filename': s.filename, 'status': s.status, 'error': s.error} for s in sources]


@router.delete('/api/v1/sources/{source_id}', status_code=204)
def delete_source(source_id: str, principal: Principal = Depends(require_role('admin'))):
    tenant = principal.tenant
    with Session.begin() as db:
        src = db.scalar(select(Source).where(Source.id == source_id, Source.tenant_id == tenant.id))
        if not src:
            raise HTTPException(404, 'Source not found')
        path = src.file_path
        db.delete(src)  # its chunks go with it (ON DELETE CASCADE)
        audit(db, tenant.id, 'knowledge.deleted', source_id,
              actor_user_id=principal.user.id if principal.user else None,
              actor_type='user' if principal.user else 'api_key')
    if path.startswith('storage:'):
        storage().delete(path.removeprefix('storage:'))
    else:
        Path(path).unlink(missing_ok=True)




class UploadIntent(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class UploadComplete(BaseModel):
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


@router.post('/api/v1/sources/uploads', status_code=201)
def begin_upload(body: UploadIntent, tenant: Tenant = Depends(tenant_for_role('admin'))):
    suffix = Path(body.filename).suffix.lower()
    if suffix not in ingest.SUPPORTED_SUFFIXES:
        raise HTTPException(422, 'Unsupported file type')
    if body.size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, 'File too large')
    source_id = uid()
    key = f'{tenant.id}/{source_id}{suffix}'
    with Session.begin() as db:
        db.add(Source(id=source_id, tenant_id=tenant.id, filename=Path(body.filename).name,
                      kind=suffix[1:], file_path='storage:' + key, status='uploading',
                      error=f'{body.size}:{body.sha256}'))
    return {'source_id': source_id, 'upload_url': storage().presign_put(key, body.content_type),
            'method': 'PUT', 'content_type': body.content_type}


@router.put('/api/v1/sources/uploads/local/{tenant_id}/{filename}', status_code=204)
async def local_upload(tenant_id: str, filename: str, request: Request,
                       tenant: Tenant = Depends(tenant_for_role('admin'))):
    if not isinstance(storage(), LocalStorage) or tenant_id != tenant.id:
        raise HTTPException(404, 'Upload not found')
    with Session() as db:
        source = db.scalar(select(Source).where(Source.tenant_id == tenant.id,
            Source.file_path == f'storage:{tenant_id}/{filename}', Source.status == 'uploading'))
    if not source:
        raise HTTPException(404, 'Upload not found')
    content = await request.body()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, 'File too large')
    storage().put(f'{tenant_id}/{filename}', content)


@router.post('/api/v1/sources/{source_id}/complete', status_code=202)
def complete_upload(source_id: str, body: UploadComplete, background: BackgroundTasks,
                    tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session() as db:
        source = db.scalar(select(Source).where(Source.id == source_id,
            Source.tenant_id == tenant.id))
        if not source:
            raise HTTPException(404, 'Source not found')
        if source.status != 'uploading':
            raise HTTPException(409, 'Upload already completed')
        expected_size, expected_hash = source.error.split(':', 1)
        key = source.file_path.removeprefix('storage:')
    if body.size != int(expected_size) or body.sha256 != expected_hash:
        raise HTTPException(422, 'Upload metadata mismatch')
    try:
        data = storage().get(key)
    except Exception:
        raise HTTPException(422, 'Uploaded object not found') from None
    if len(data) != body.size or hashlib.sha256(data).hexdigest() != body.sha256:
        raise HTTPException(422, 'Uploaded object failed integrity check')
    with Session.begin() as db:
        source = db.scalar(select(Source).where(Source.id == source_id,
            Source.tenant_id == tenant.id).with_for_update())
        if source.status != 'uploading':
            raise HTTPException(409, 'Upload already completed')
        source.status, source.error = 'queued', None
    background.add_task(ingest.ingest, source_id)
    return {'source_id': source_id, 'status': 'queued'}
