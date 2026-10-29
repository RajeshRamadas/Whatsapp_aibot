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

@router.post('/api/v1/chat/{public_key}')
def public_chat(public_key: str, body: ChatInput, request: Request):
    with Session() as db:
        t = db.scalar(select(Tenant).where(Tenant.public_key == public_key))
        if not t:
            raise HTTPException(404, 'Unknown chatbot')
        origin = request.headers.get('origin')
        if origin and origin not in origins(t):
            raise HTTPException(403, 'Origin not allowed')
        conv = None
        if body.conversation_id:
            conv = db.scalar(select(Conversation).where(Conversation.id == body.conversation_id,
                                                        Conversation.tenant_id == t.id,
                                                        Conversation.channel == 'web'))
            if not conv:
                raise HTTPException(404, 'Conversation not found')
    # The public key is an identifier, not a secret, so requests without an Origin
    # (scripts, curl) are allowed too: the rate limit is what bounds abuse.
    client = request.client.host if request.client else 'unknown'
    if not rag.chat_limiter.allow(f'web:{t.id}:{client}'):
        raise HTTPException(429, 'Too many messages. Please wait a minute.')
    try:
        result = rag.answer(t, conv.id if conv else None, body.message)
    except llm.ProviderNotConfigured as e:
        log.error('Chat provider not configured: %s', e)
        raise HTTPException(503, 'The chatbot is not configured yet') from None
    is_new = conv is None
    conv = conv or Conversation(id=uid(), tenant_id=t.id, channel='web')
    # Saved only after a successful answer, so failed requests leave nothing behind.
    rag.save_exchange(conv, ('user', body.message), ('assistant', result.text), create=is_new)
    return {'conversation_id': conv.id, 'answer': result.text, 'sources': result.sources}


