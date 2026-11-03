"""Manage WhatsApp templates and queue agent sends."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..db import Channel, Conversation, Message, Session, Template
from ..services.channels import access_token, whatsapp_channel
from ..services.events import publish_sync
from ..services.meta_signup import MetaSignup, MetaSignupError
from ..services.templates import eligible_contact, list_meta, render, template_for_send, upsert
from .inbox import can_reply
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1')
TextValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class Binding(BaseModel):
    waba_id: Annotated[str, StringConstraints(pattern=r'^[0-9]{1,32}$')]


class TemplateInput(BaseModel):
    name: Annotated[str, StringConstraints(pattern=r'^[a-z0-9_]{1,512}$')]
    language: Annotated[str, StringConstraints(pattern=r'^[a-z]{2,3}(?:_[A-Z]{2})?$')]
    category: Literal['UTILITY', 'MARKETING']
    body: str = Field(min_length=1, max_length=1024)
    examples: list[TextValue] = Field(default_factory=list, max_length=20)


class TemplateSend(BaseModel):
    template_id: str = Field(min_length=1, max_length=36)
    values: list[TextValue] = Field(default_factory=list, max_length=20)


def channel_for(principal, *, bound=True):
    with Session() as db:
        channel = whatsapp_channel(db, principal.tenant.id)
    if not channel or (bound and not channel.waba_id):
        raise HTTPException(409, 'Connect a WhatsApp Business Account first')
    return channel


def credentials(channel):
    try:
        return access_token(channel.external_id, channel.tenant_id)
    except (RuntimeError, ValueError):
        raise HTTPException(503, 'WhatsApp credentials are unavailable') from None


def current_channel(db, channel):
    row = db.scalar(select(Channel).where(Channel.id == channel.id, Channel.tenant_id == channel.tenant_id)
                    .with_for_update())
    if row is None or row.waba_id != channel.waba_id:
        raise HTTPException(409, 'The WhatsApp channel changed. Refresh and try again.')
    return row


@router.put('/whatsapp/templates/account')
def bind(body: Binding, principal: Principal = Depends(require_role('admin'))):
    channel = channel_for(principal, bound=False)
    if channel.waba_id and channel.waba_id != body.waba_id:
        raise HTTPException(409, 'Unlink the channel before changing its business account')
    try:
        MetaSignup().verify_phone(body.waba_id, channel.external_id, credentials(channel))
    except MetaSignupError as exc:
        raise HTTPException(502, str(exc)) from None
    try:
        with Session.begin() as db:
            current_channel(db, channel).waba_id = body.waba_id
    except IntegrityError:
        raise HTTPException(409, 'This business account is already linked') from None
    return {'waba_id': body.waba_id}


@router.get('/whatsapp/templates')
def templates(approved: bool = False, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
              principal: Principal = Depends(require_role('agent'))):
    with Session() as db:
        channel = whatsapp_channel(db, principal.tenant.id)
        if channel is None:
            return []
        query = select(Template).where(Template.channel_id == channel.id)
        if approved:
            query = query.where(Template.status == 'APPROVED', Template.body.is_not(None))
        rows = db.scalars(query.order_by(Template.name, Template.language, Template.id).offset(offset).limit(limit))
        return [{'id': row.id, 'name': row.name, 'language': row.language, 'category': row.category,
                 'status': row.status, 'body': row.body} for row in rows]


@router.post('/whatsapp/templates/sync')
def sync_templates(principal: Principal = Depends(require_role('admin'))):
    channel = channel_for(principal)
    try:
        rows = list_meta(channel.waba_id, credentials(channel))
    except MetaSignupError as exc:
        raise HTTPException(502, str(exc)) from None
    with Session.begin() as db:
        current_channel(db, channel)
        db.execute(update(Template).where(Template.channel_id == channel.id).values(status='MISSING'))
        for row in rows:
            upsert(db, channel.id, row)
    return {'synced': len(rows)}


@router.post('/whatsapp/templates', status_code=201)
def create_template(body: TemplateInput, principal: Principal = Depends(require_role('admin'))):
    channel = channel_for(principal)
    try:
        render(body.body, body.examples)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    component = {'type': 'BODY', 'text': body.body}
    if body.examples:
        component['example'] = {'body_text': [body.examples]}
    data = {'name': body.name, 'language': body.language, 'category': body.category, 'components': [component]}
    try:
        result = MetaSignup().request(f'{channel.waba_id}/message_templates',
                                      token=credentials(channel), payload=data)
        if not result.get('id'):
            raise MetaSignupError('Meta did not confirm template creation')
    except MetaSignupError as exc:
        raise HTTPException(502, str(exc)) from None
    with Session.begin() as db:
        current_channel(db, channel)
        upsert(db, channel.id, {**data, 'id': result['id'], 'status': result.get('status', 'PENDING')})
    return {'meta_id': str(result['id']), 'status': result.get('status', 'PENDING')}


@router.post('/inbox/conversations/{conversation_id}/template')
def queue_template(conversation_id: str, body: TemplateSend,
                   principal: Principal = Depends(require_role('agent')),
                   idempotency_key: str = Header(min_length=1, max_length=128)):
    tenant_id = principal.tenant.id
    payload = body.model_dump()
    with Session.begin() as db:
        conversation = db.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.tenant_id == tenant_id,
        ).with_for_update())
        if conversation is None:
            raise HTTPException(404, 'Conversation not found')
        existing = db.scalar(select(Message).where(
            Message.conversation_id == conversation_id, Message.reply_key == idempotency_key,
        ))
        if existing:
            if existing.template_payload != payload:
                raise HTTPException(409, 'Idempotency key already used for another reply')
            return {'status': existing.delivery_status, 'message_id': existing.id}
        if conversation.channel != 'whatsapp' or can_reply(conversation) or conversation.status == 'closed':
            raise HTTPException(409, 'An open WhatsApp conversation outside the reply window is required')
        if not eligible_contact(db, conversation):
            raise HTTPException(409, 'This contact has not opted in to WhatsApp templates')
        channel = whatsapp_channel(db, tenant_id)
        template = template_for_send(db, channel.id, body.template_id) if channel else None
        if template is None:
            raise HTTPException(404, 'Approved template not found')
        try:
            preview = render(template.body, body.values)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        message = Message(conversation_id=conversation.id, role='agent', direction='outbound', kind='template',
                          author_user_id=principal.user.id if principal.user else None, content=preview,
                          template_payload=payload, delivery_status='queued', reply_key=idempotency_key)
        db.add(message)
        conversation.status, conversation.handoff = 'agent', True
        db.flush()
        message_id = message.id
    publish_sync(tenant_id, 'message.created', {'conversation_id': conversation_id})
    publish_sync(tenant_id, 'conversation.updated', {'conversation_id': conversation_id})
    return {'status': 'queued', 'message_id': message_id}
