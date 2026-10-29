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

@router.get('/api/v1/conversations')
def list_conversations(handoff: bool | None = None, limit: int = Query(50, ge=1, le=200),
                       tenant: Tenant = Depends(admin_tenant)):
    query = select(Conversation).where(Conversation.tenant_id == tenant.id)
    if handoff is not None:
        query = query.where(Conversation.handoff == handoff)
    with Session() as db:
        rows = db.scalars(query.order_by(Conversation.created_at.desc()).limit(limit)).all()
    return [{'id': c.id, 'channel': c.channel, 'user': c.external_user, 'handoff': c.handoff,
             'created_at': c.created_at} for c in rows]


def tenant_conversation(db, tenant: Tenant, conversation_id: str) -> Conversation:
    conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                                                Conversation.tenant_id == tenant.id))
    if not conv:
        raise HTTPException(404, 'Conversation not found')
    return conv


@router.get('/api/v1/conversations/{conversation_id}/messages')
def conversation_messages(conversation_id: str, tenant: Tenant = Depends(admin_tenant)):
    with Session() as db:
        conv = tenant_conversation(db, tenant, conversation_id)
        rows = db.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)).all()
    return [{'role': m.role, 'content': m.content, 'created_at': m.created_at} for m in rows]


@router.post('/api/v1/conversations/{conversation_id}/handoff')
def set_handoff(conversation_id: str, active: bool = True, tenant: Tenant = Depends(admin_tenant)):
    """active=true: a human takes over and the bot stops replying. active=false: hand back to the bot."""
    with Session.begin() as db:
        tenant_conversation(db, tenant, conversation_id).handoff = active
    return {'id': conversation_id, 'handoff': active}


@router.post('/api/v1/conversations/{conversation_id}/reply')
def agent_reply(conversation_id: str, body: AgentReply, tenant: Tenant = Depends(admin_tenant)):
    """Send a message from a human agent to a WhatsApp user."""
    with Session() as db:
        conv = tenant_conversation(db, tenant, conversation_id)
    if conv.channel != 'whatsapp' or not tenant.whatsapp_phone_number_id:
        raise HTTPException(409, 'Agent replies are only available for WhatsApp conversations')
    if not settings.whatsapp_enabled:
        raise HTTPException(503, 'WhatsApp is not configured')
    # Meta only delivers free-form messages within 24 hours of the user's last message.
    whatsapp.send_text(tenant.whatsapp_phone_number_id, conv.external_user, body.message)
    rag.save_exchange(conv, ('agent', body.message))
    return {'status': 'sent'}


