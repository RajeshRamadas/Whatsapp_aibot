"""Idempotent sends and accuracy runs reuse existing durable dispatchers."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from ..api.evals import RunSummary
from ..api.inbox import can_reply
from ..config import settings
from ..db import Channel, Conversation, Message, Session, Template, now
from ..models.evaluations import EvalRun
from ..services import evaluation_runs
from ..services.appointments import AppointmentError
from ..services.audit import audit
from ..services.channels import whatsapp_channel
from ..services.events import publish_sync
from ..services.templates import eligible_contact, render, template_for_send
from .auth import PublicPrincipal, require
from .contracts import PublicError, post
from .resources import Cursor, Key, Limit, MessageView, Page, owned, page, view

router = APIRouter()


class TemplateView(BaseModel):
    id: str
    name: str
    language: str
    category: str
    status: str
    body: str


@router.get('/templates', response_model=Page[TemplateView], tags=['messages'])
def templates(limit: Limit = 30, cursor: Cursor = None, p: PublicPrincipal = Depends(require('messages:send'))):
    with Session() as db:
        query = select(Template).join(Channel, Channel.id == Template.channel_id).where(
            Channel.tenant_id == p.tenant.id, Template.status == 'APPROVED', Template.body.is_not(None))
        return page(db, query, Template.id, p.tenant.id, 'templates', TemplateView, limit, cursor)


class TextSend(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['text']
    conversation_id: UUID
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]


class TemplateSend(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['template']
    conversation_id: UUID
    template_id: UUID
    values: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=20)


@router.post('/messages', response_model=MessageView, status_code=201, tags=['messages'])
def send(body: Annotated[TextSend | TemplateSend, Field(discriminator='type')], key: Key,
         p: PublicPrincipal = Depends(require('messages:send'))):
    def save(db, identity):
        conv = owned(db, Conversation, p.tenant.id, body.conversation_id, lock=True)
        if conv.status == 'closed':
            raise PublicError(409, 'conversation_closed', 'Reopen the conversation before sending')
        template_payload, content = None, body.text if isinstance(body, TextSend) else ''
        if conv.channel == 'whatsapp':
            channel = whatsapp_channel(db, p.tenant.id)
            if not p.tenant.whatsapp_phone_number_id or not channel:
                raise PublicError(409, 'channel_unavailable', 'This conversation has no linked WhatsApp channel')
            if not settings.whatsapp_enabled or not (settings.whatsapp_access_token or channel.credentials_enc):
                raise PublicError(503, 'channel_unavailable', 'WhatsApp is not configured')
            if isinstance(body, TextSend) and not can_reply(conv):
                raise PublicError(409, 'whatsapp_window_expired', 'The 24-hour customer service window has expired')
            if isinstance(body, TemplateSend):
                if can_reply(conv):
                    raise PublicError(409, 'window_open', 'Use text during the customer service window')
                if not eligible_contact(db, conv):
                    raise PublicError(409, 'consent_required', 'Contact must opt in to WhatsApp templates')
                template = template_for_send(db, channel.id, str(body.template_id))
                if template is None:
                    raise PublicError(404, 'not_found', 'Approved template not found')
                try:
                    content = render(template.body, body.values)
                except ValueError as exc:
                    raise PublicError(422, 'invalid_template_values', str(exc)) from None
                template_payload = {'template_id': str(body.template_id), 'values': body.values}
        elif conv.channel != 'web' or isinstance(body, TemplateSend):
            raise PublicError(409, 'unsupported_channel', 'This message type requires a supported channel')
        row = Message(conversation_id=conv.id, role='agent', direction='outbound', kind=body.type,
                      content=content, template_payload=template_payload, reply_key=identity,
                      delivery_status='sent' if conv.channel == 'web' else 'queued')
        db.add(row)
        conv.handoff, conv.status, conv.last_message_at = True, 'agent', now()
        db.flush()
        return 201, view(row, MessageView), {'Location': '/api/v1/public/messages/' + str(row.id)}
    result = post(p, key, '/messages', body.model_dump(mode='json'), save)
    if result.status_code == 201 and result.headers['Idempotency-Replayed'] == 'false':
        publish_sync(p.tenant.id, 'message.created', {'conversation_id': str(body.conversation_id)})
        publish_sync(p.tenant.id, 'conversation.updated', {'conversation_id': str(body.conversation_id)})
    return result


class RunInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = 'API accuracy run'
    case_ids: list[UUID] | None = Field(None, min_length=1, max_length=200)
    confirm_cost: Literal[True]


class Confirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: Literal[True]


@router.post('/eval-runs', response_model=RunSummary, status_code=202, tags=['eval-runs'])
def start_run(body: RunInput, key: Key, p: PublicPrincipal = Depends(require('evals:write'))):
    def save(db, identity):
        values = body.model_dump(mode='json', exclude={'confirm_cost'}) | {'request_key': identity}
        try:
            result = evaluation_runs.create_in_session(db, p.tenant.id, values, actor=('api_key', None))
        except AppointmentError as exc:
            raise PublicError(exc.status, 'run_unavailable', str(exc)) from None
        return 202, result, {'Location': '/api/v1/public/eval-runs/' + result['id']}
    return post(p, key, '/eval-runs', body.model_dump(mode='json'), save)


@router.post('/eval-runs/{identity}/cancel', response_model=RunSummary, tags=['eval-runs'])
def cancel_run(identity: UUID, body: Confirm, key: Key, p: PublicPrincipal = Depends(require('evals:write'))):
    def save(db, operation_id):
        run = owned(db, EvalRun, p.tenant.id, identity, lock=True)
        if run.state in ('queued', 'running'):
            run.state, run.finished_at = 'cancelled', now()
            audit(db, p.tenant.id, 'eval_run.cancelled', run.id, actor_type='api_key')
        return 200, evaluation_runs.view(run), {}
    return post(p, key, '/eval-runs/' + str(identity) + '/cancel', body.model_dump(), save)
