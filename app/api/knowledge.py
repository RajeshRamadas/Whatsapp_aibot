# ruff: noqa: F401
import json
import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select

from .. import evaluate, ingest, llm, rag, whatsapp
from ..config import settings
from ..db import Conversation, EvalCase, Message, Session, Source, Tenant, uid
from .principal import tenant_for_role
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
async def upload(background: BackgroundTasks, file: UploadFile = File(...), tenant: Tenant = Depends(tenant_for_role('admin'))):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ingest.SUPPORTED_SUFFIXES:
        raise HTTPException(415, 'Unsupported file type')
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, 'File too large')
    source_id = uid()
    directory = UPLOADS / tenant.id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (source_id + suffix)
    path.write_bytes(content)
    with Session.begin() as db:
        db.add(Source(id=source_id, tenant_id=tenant.id, filename=Path(file.filename or 'file').name[:255],
                      kind=suffix[1:], file_path=str(path), status='queued'))
    background.add_task(ingest.ingest, source_id)  # Dev only: use a durable queue in production.
    return {'source_id': source_id, 'status': 'queued'}


@router.get('/api/v1/sources')
def list_sources(tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session() as db:
        sources = db.scalars(select(Source).where(Source.tenant_id == tenant.id).order_by(Source.created_at))
        return [{'id': s.id, 'filename': s.filename, 'status': s.status, 'error': s.error} for s in sources]


@router.delete('/api/v1/sources/{source_id}', status_code=204)
def delete_source(source_id: str, tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session.begin() as db:
        src = db.scalar(select(Source).where(Source.id == source_id, Source.tenant_id == tenant.id))
        if not src:
            raise HTTPException(404, 'Source not found')
        path = Path(src.file_path)
        db.delete(src)  # its chunks go with it (ON DELETE CASCADE)
    path.unlink(missing_ok=True)


