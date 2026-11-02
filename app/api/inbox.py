"""Inbox actions with workspace and role enforcement."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from .. import rag, whatsapp
from ..config import settings
from ..db import CannedReply, Contact, Conversation, Membership, Message, Session, now, uid
from ..services.events import publish_sync
from .principal import Principal, require_role

router = APIRouter()


def conversation(db, tenant_id: str, conversation_id: str) -> Conversation:
    row = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                                               Conversation.tenant_id == tenant_id))
    if row is None:
        raise HTTPException(404, 'Conversation not found')
    return row


def can_reply(conv: Conversation) -> bool:
    return bool(conv.last_inbound_at and conv.last_inbound_at >= now() - timedelta(hours=24))


def view(conv: Conversation) -> dict:
    return {'id': conv.id, 'channel': conv.channel, 'user': conv.external_user, 'handoff': conv.handoff,
            'contact_id': conv.contact_id, 'status': conv.status, 'assignee_user_id': conv.assignee_user_id,
            'labels': conv.labels, 'last_inbound_at': conv.last_inbound_at, 'last_message_at': conv.last_message_at,
            'created_at': conv.created_at, 'can_reply_freely': can_reply(conv)}


class Assign(BaseModel):
    user_id: str | None = None


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Labels(BaseModel):
    labels: list[str] = Field(max_length=20)


class Reply(BaseModel):
    message: str = Field(min_length=1, max_length=whatsapp.MAX_TEXT)


class CannedInput(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=4000)


@router.get('/api/v1/inbox/conversations')
def inbox_list(status: str | None = None, channel: str | None = None, assignee: str | None = None,
               label: str | None = None, search: str | None = Query(None, max_length=200),
               cursor: str | None = None, limit: int = Query(30, ge=1, le=100),
               principal: Principal = Depends(require_role('viewer'))):
    tenant_id = principal.tenant.id
    query = select(Conversation).where(Conversation.tenant_id == tenant_id)
    if status:
        query = query.where(Conversation.status == status)
    if channel:
        query = query.where(Conversation.channel == channel)
    if assignee:
        query = query.where(Conversation.assignee_user_id == assignee)
    if label:
        query = query.where(Conversation.labels.contains([label]))
    if search:
        query = query.outerjoin(Contact, Conversation.contact_id == Contact.id).where(
            or_(Conversation.external_user.ilike(f'%{search}%'), Contact.name.ilike(f'%{search}%')))
    with Session() as db:
        counts = dict(db.execute(select(Conversation.status, func.count()).where(
            Conversation.tenant_id == tenant_id).group_by(Conversation.status)).all())
        if cursor:
            previous = db.scalar(select(Conversation).where(Conversation.id == cursor,
                                                            Conversation.tenant_id == tenant_id))
            if previous is None:
                raise HTTPException(422, 'Invalid cursor')
            query = query.where(or_(Conversation.created_at < previous.created_at,
                                    (Conversation.created_at == previous.created_at) &
                                    (Conversation.id < previous.id)))
        rows = db.scalars(query.order_by(Conversation.created_at.desc(), Conversation.id.desc())
                          .limit(limit + 1)).all()
    return {'items': [view(row) for row in rows[:limit]],
            'next_cursor': rows[limit - 1].id if len(rows) > limit else None, 'counts': counts}


@router.post('/api/v1/inbox/conversations/{conversation_id}/take-over')
def take_over(conversation_id: str, principal: Principal = Depends(require_role('agent'))):
    if principal.user is None:
        raise HTTPException(403, 'User session required to take ownership')
    with Session.begin() as db:
        conv = conversation(db, principal.tenant.id, conversation_id)
        conv.handoff, conv.status, conv.assignee_user_id = True, 'agent', principal.user.id
        result = view(conv)
    publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': conversation_id})
    return result


@router.post('/api/v1/inbox/conversations/{conversation_id}/assign')
def assign(conversation_id: str, body: Assign, principal: Principal = Depends(require_role('agent'))):
    with Session.begin() as db:
        conv = conversation(db, principal.tenant.id, conversation_id)
        if body.user_id is not None and not db.scalar(select(Membership).where(
                Membership.user_id == body.user_id, Membership.tenant_id == principal.tenant.id)):
            raise HTTPException(422, 'Assignee must belong to this workspace')
        conv.assignee_user_id = body.user_id
        conv.handoff = True
        conv.status = 'agent' if body.user_id else 'waiting'
        result = view(conv)
    publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': conversation_id})
    return result


@router.post('/api/v1/inbox/conversations/{conversation_id}/note')
def add_note(conversation_id: str, body: Note, principal: Principal = Depends(require_role('agent'))):
    with Session.begin() as db:
        conv = conversation(db, principal.tenant.id, conversation_id)
        db.add(Message(conversation_id=conv.id, role='agent', direction='outbound', kind='note',
                       author_user_id=principal.user.id if principal.user else None, content=body.text))
        conv.last_message_at = now()
    publish_sync(principal.tenant.id, 'message.created', {'conversation_id': conversation_id})
    return {'status': 'saved'}


@router.post('/api/v1/inbox/conversations/{conversation_id}/reply')
def reply(conversation_id: str, body: Reply, principal: Principal = Depends(require_role('agent'))):
    with Session() as db:
        conv = conversation(db, principal.tenant.id, conversation_id)
        if conv.channel != 'whatsapp' or not principal.tenant.whatsapp_phone_number_id:
            raise HTTPException(409, 'Only WhatsApp conversations support agent replies')
        if not can_reply(conv):
            raise HTTPException(409, {'code': 'whatsapp_window_expired',
                                      'message': 'The 24-hour customer service window has expired'})
        if not settings.whatsapp_enabled:
            raise HTTPException(503, 'WhatsApp is not configured')
    message_id = whatsapp.send_text(principal.tenant.whatsapp_phone_number_id, conv.external_user, body.message)
    rag.save_exchange(conv, ('agent', body.message), channel_message_id=message_id)
    with Session.begin() as db:
        current = conversation(db, principal.tenant.id, conversation_id)
        current.handoff, current.status = True, 'agent'
    publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': conversation_id})
    return {'status': 'sent', 'message_id': message_id}


@router.post('/api/v1/inbox/conversations/{conversation_id}/hand-back')
def hand_back(conversation_id: str, principal: Principal = Depends(require_role('agent'))):
    with Session.begin() as db:
        conv = conversation(db, principal.tenant.id, conversation_id)
        conv.handoff, conv.status, conv.assignee_user_id = False, 'bot', None
        result = view(conv)
    publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': conversation_id})
    return result


@router.post('/api/v1/inbox/conversations/{conversation_id}/close')
def close(conversation_id: str, principal: Principal = Depends(require_role('agent'))):
    with Session.begin() as db:
        conv = conversation(db, principal.tenant.id, conversation_id)
        conv.status, conv.handoff = 'closed', True
        result = view(conv)
    publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': conversation_id})
    return result


@router.post('/api/v1/inbox/conversations/{conversation_id}/reopen')
def reopen(conversation_id: str, principal: Principal = Depends(require_role('agent'))):
    with Session.begin() as db:
        conv = conversation(db, principal.tenant.id, conversation_id)
        conv.status, conv.handoff = 'waiting', True
        result = view(conv)
    publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': conversation_id})
    return result


@router.put('/api/v1/inbox/conversations/{conversation_id}/labels')
def update_labels(conversation_id: str, body: Labels, principal: Principal = Depends(require_role('agent'))):
    if any(not label.strip() or len(label) > 50 for label in body.labels):
        raise HTTPException(422, 'Labels must be 1-50 characters')
    with Session.begin() as db:
        conv = conversation(db, principal.tenant.id, conversation_id)
        conv.labels = list(dict.fromkeys(body.labels))
        result = view(conv)
    publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': conversation_id})
    return result


@router.post('/api/v1/inbox/conversations/{conversation_id}/typing')
def typing(conversation_id: str, principal: Principal = Depends(require_role('agent'))):
    with Session() as db:
        conversation(db, principal.tenant.id, conversation_id)
    publish_sync(principal.tenant.id, 'typing.updated',
                 {'conversation_id': conversation_id, 'user_id': principal.user.id if principal.user else None,
                  'expires_in_seconds': 5})
    return {'status': 'ok', 'expires_in_seconds': 5}


@router.get('/api/v1/inbox/canned-replies')
def list_canned(principal: Principal = Depends(require_role('viewer'))):
    with Session() as db:
        rows = db.scalars(select(CannedReply).where(CannedReply.tenant_id == principal.tenant.id)
                          .order_by(CannedReply.title)).all()
    return [{'id': row.id, 'title': row.title, 'body': row.body} for row in rows]


@router.post('/api/v1/inbox/canned-replies', status_code=201)
def create_canned(body: CannedInput, principal: Principal = Depends(require_role('admin'))):
    reply_id = uid()
    with Session.begin() as db:
        db.add(CannedReply(id=reply_id, tenant_id=principal.tenant.id, title=body.title, body=body.body))
    return {'id': reply_id, **body.model_dump()}


@router.put('/api/v1/inbox/canned-replies/{reply_id}')
def update_canned(reply_id: str, body: CannedInput, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = db.scalar(select(CannedReply).where(CannedReply.id == reply_id,
                                                  CannedReply.tenant_id == principal.tenant.id))
        if row is None:
            raise HTTPException(404, 'Canned reply not found')
        row.title, row.body = body.title, body.body
    return {'id': reply_id, **body.model_dump()}


@router.delete('/api/v1/inbox/canned-replies/{reply_id}', status_code=204)
def delete_canned(reply_id: str, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = db.scalar(select(CannedReply).where(CannedReply.id == reply_id,
                                                  CannedReply.tenant_id == principal.tenant.id))
        if row is None:
            raise HTTPException(404, 'Canned reply not found')
        db.delete(row)
