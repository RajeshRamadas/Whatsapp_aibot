# ruff: noqa: F401
import json
import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select

from .. import evaluate, ingest, llm, rag, whatsapp
from ..config import settings
from ..db import Conversation, EvalCase, Message, Session, Source, Tenant, uid
from ..jobs.queue import enqueue
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
async def whatsapp_webhook(request: Request):
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
        event_id = await run_in_threadpool(whatsapp.accept, message)
        if event_id:
            try:
                await enqueue('handle_message', event_id=event_id, _job_id=event_id)
            except (OSError, ConnectionError, TimeoutError, RuntimeError) as exc:
                raise HTTPException(503, 'Inbound queue unavailable') from exc
    return {'status': 'ok'}


