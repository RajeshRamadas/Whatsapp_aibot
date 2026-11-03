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
from ..services.events import publish_sync
from .inbox import Reply, reply
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

@router.get('/api/v1/conversations')
def list_conversations(handoff: bool | None = None, limit: int = Query(50, ge=1, le=200),
                       tenant: Tenant = Depends(tenant_for_role('viewer'))):
    query = select(Conversation).where(Conversation.tenant_id == tenant.id)
    if handoff is not None:
        query = query.where(Conversation.handoff == handoff)
    with Session() as db:
        rows = db.scalars(query.order_by(Conversation.created_at.desc()).limit(limit)).all()
    return [{'id': c.id, 'channel': c.channel, 'user': c.external_user, 'handoff': c.handoff,
             'contact_id': c.contact_id, 'status': c.status, 'assignee_user_id': c.assignee_user_id,
             'labels': c.labels, 'last_inbound_at': c.last_inbound_at,
             'last_message_at': c.last_message_at, 'created_at': c.created_at} for c in rows]


def tenant_conversation(db, tenant: Tenant, conversation_id: str) -> Conversation:
    conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                                                Conversation.tenant_id == tenant.id))
    if not conv:
        raise HTTPException(404, 'Conversation not found')
    return conv


@router.get('/api/v1/conversations/{conversation_id}/messages')
def conversation_messages(conversation_id: str, tenant: Tenant = Depends(tenant_for_role('viewer'))):
    with Session() as db:
        conv = tenant_conversation(db, tenant, conversation_id)
        rows = db.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)).all()
    return [{'role': m.role, 'content': m.content, 'created_at': m.created_at,
             'direction': m.direction, 'kind': m.kind, 'author_user_id': m.author_user_id,
             'channel_message_id': m.channel_message_id, 'delivery_status': m.delivery_status,
             'error': m.error} for m in rows]


@router.post('/api/v1/conversations/{conversation_id}/handoff')
def set_handoff(conversation_id: str, active: bool = True, tenant: Tenant = Depends(tenant_for_role('agent'))):
    """active=true: a human takes over and the bot stops replying. active=false: hand back to the bot."""
    with Session.begin() as db:
        conv = tenant_conversation(db, tenant, conversation_id)
        conv.handoff = active
        conv.status = 'agent' if active else 'bot'
    publish_sync(tenant.id, 'conversation.updated', {'conversation_id': conversation_id})
    return {'id': conversation_id, 'handoff': active}


@router.post('/api/v1/conversations/{conversation_id}/reply')
def agent_reply(conversation_id: str, body: AgentReply, principal: Principal = Depends(require_role('agent')),
                idempotency_key: str | None = Header(None, min_length=1, max_length=128)):
    return reply(conversation_id, Reply(message=body.message), principal, idempotency_key)
