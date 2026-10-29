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
from .shared import (
    AgentReply, ChatInput, CreateTenant, EvalCasesInput, UpdateTenant, UPLOADS, WIDGET,
    admin_tenant, eval_view, origins, tenant_view,
)

log = logging.getLogger(__name__)
router = APIRouter()

@router.get('/health')
def health():
    return {'status': 'ok'}


@router.get('/widget.js')
def widget_js():
    return FileResponse(WIDGET, media_type='application/javascript')
