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

@router.get('/webhooks/whatsapp')
def whatsapp_verify(mode: str = Query('', alias='hub.mode'), token: str = Query('', alias='hub.verify_token'),
                    challenge: str = Query('', alias='hub.challenge')):
    if not settings.whatsapp_enabled:
        raise HTTPException(404)
    if mode == 'subscribe' and secrets.compare_digest(token.encode(), settings.whatsapp_verify_token.encode()):
        return PlainTextResponse(challenge)
    raise HTTPException(403)


@router.post('/webhooks/whatsapp')
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    if not settings.whatsapp_enabled:
        raise HTTPException(404)
    body = await request.body()
    if not whatsapp.valid_signature(body, request.headers.get('x-hub-signature-256')):
        raise HTTPException(401, 'Invalid signature')
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, 'Invalid JSON') from None
    for message in whatsapp.parse_messages(payload):
        tenant_id = await run_in_threadpool(whatsapp.accept, message)
        if tenant_id:
            background.add_task(whatsapp.handle, tenant_id, message)  # Dev only: use a durable queue in production.
    return {'status': 'ok'}


