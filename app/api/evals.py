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

@router.post('/api/v1/evals', status_code=201)
def add_eval_cases(body: EvalCasesInput, tenant: Tenant = Depends(tenant_for_role('admin'))):
    cases = [EvalCase(tenant_id=tenant.id, **c.model_dump()) for c in body.cases]
    with Session.begin() as db:
        db.add_all(cases)
    return [eval_view(c) for c in cases]


@router.get('/api/v1/evals')
def list_eval_cases(tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session() as db:
        return [eval_view(c) for c in db.scalars(select(EvalCase).where(EvalCase.tenant_id == tenant.id)
                                                  .order_by(EvalCase.created_at))]


@router.delete('/api/v1/evals/{case_id}', status_code=204)
def delete_eval_case(case_id: str, tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session.begin() as db:
        case = db.scalar(select(EvalCase).where(EvalCase.id == case_id, EvalCase.tenant_id == tenant.id))
        if not case:
            raise HTTPException(404, 'Test question not found')
        db.delete(case)


@router.post('/api/v1/evals/run')
def run_evals(tenant: Tenant = Depends(tenant_for_role('admin'))):
    """Ask every test question and score the answers. Each question is a real (billed) provider call."""
    with Session() as db:
        cases = db.scalars(select(EvalCase).where(EvalCase.tenant_id == tenant.id)
                           .order_by(EvalCase.created_at)).all()
    if not cases:
        raise HTTPException(409, 'Add test questions first (POST /api/v1/evals)')
    return evaluate.run(tenant, cases)


