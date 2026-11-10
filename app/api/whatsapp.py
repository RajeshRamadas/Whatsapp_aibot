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
from ..services.campaign_delivery import quality_event
from ..services.templates import record_status as record_template_status
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
    for entry in payload.get('entry') or []:
        for change in entry.get('changes') or []:
            if change.get('field') in ('phone_number_quality_update', 'business_capability_update') and entry.get('id'):
                await run_in_threadpool(quality_event, str(entry['id']), change.get('value') or {})
            if change.get('field') != 'message_template_status_update':
                continue
            value = change.get('value') or {}
            status = value.get('event')
            meta_id = value.get('message_template_id')
            if isinstance(status, str) and 1 <= len(status) <= 32 and meta_id and entry.get('id'):
                await run_in_threadpool(record_template_status, str(entry['id']), str(meta_id), status.upper())
    for status in whatsapp.parse_statuses(payload):
        await run_in_threadpool(whatsapp.record_status, status)
    for message in whatsapp.parse_messages(payload):
        event_id = await run_in_threadpool(whatsapp.accept, message)
        if event_id:
            try:
                await enqueue('handle_message', event_id=event_id, _job_id=event_id)
            except (OSError, ConnectionError, TimeoutError, RuntimeError) as exc:
                raise HTTPException(503, 'Inbound queue unavailable') from exc
    return {'status': 'ok'}


