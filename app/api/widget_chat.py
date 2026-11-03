"""Public widget configuration, durable turns and browser delivery."""
import asyncio
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import rag
from ..db import Conversation, Message, Session, Tenant, WidgetTurn, uid
from .principal import tenant_for_role
from .shared import origins

router = APIRouter()
BASE = '/api/v1/chat/{public_key}/widget'


class Theme(BaseModel):
    color: Annotated[str, StringConstraints(pattern=r'^#[0-9a-fA-F]{6}$')] = '#064e3b'
    position: Literal['left', 'right'] = 'right'
    icon: Literal['chat', 'sparkle', 'help'] = 'chat'
    theme: Literal['light', 'dark', 'auto'] = 'auto'
    greeting: str = Field('Hi! How can we help?', max_length=160)
    show_greeting: bool = True
    avatar_url: Annotated[str, StringConstraints(pattern=r'^(?:https://[^\s<>]+)?$', max_length=1024)] = ''


class TurnInput(BaseModel):
    request_id: UUID
    conversation_id: UUID | None = None
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    handoff: bool = False


class Feedback(BaseModel):
    vote: Literal['up', 'down']


def widget_tenant(public_key, request):
    with Session() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.public_key == public_key))
    if tenant is None:
        raise HTTPException(404, 'Unknown chatbot')
    if (origin := request.headers.get('origin')) and origin not in origins(tenant):
        raise HTTPException(403, 'Origin not allowed')
    return tenant


def view(turn):
    return {'id': turn.id, 'conversation_id': turn.conversation_id, 'state': turn.state,
            'answer': turn.answer, 'sources': turn.sources, 'feedback': turn.feedback}


def find_turn(db, tenant_id, turn_id, lock=False):
    query = select(WidgetTurn).join(Conversation, Conversation.id == WidgetTurn.conversation_id).where(
        Conversation.tenant_id == tenant_id, Conversation.channel == 'web', WidgetTurn.id == str(turn_id),
    )
    return db.scalar(query.with_for_update(of=WidgetTurn) if lock else query)


def turn_status(tenant_id, turn_id):
    with Session() as db:
        turn = find_turn(db, tenant_id, turn_id)
        if turn is None:
            raise HTTPException(404, 'Widget message not found')
        return view(turn)


@router.get(BASE + '/config')
def config(public_key: str, request: Request):
    tenant = widget_tenant(public_key, request)
    return {**Theme(**tenant.widget_theme).model_dump(), 'name': tenant.bot_name}


@router.get('/api/v1/widget-settings')
def settings(tenant: Tenant = Depends(tenant_for_role('admin'))):
    return {**Theme(**tenant.widget_theme).model_dump(), 'name': tenant.bot_name, 'public_key': tenant.public_key}


@router.put('/api/v1/widget-settings')
def update_settings(body: Theme, tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session.begin() as db:
        db.get(Tenant, tenant.id).widget_theme = body.model_dump()
    return body


def replay(turn, body):
    if (turn.message != body.message or turn.handoff != body.handoff
            or (body.conversation_id and turn.conversation_id != str(body.conversation_id))):
        raise HTTPException(409, 'Request ID was already used for a different message')
    return view(turn)


@router.post(BASE + '/turns', status_code=202)
def queue_turn(public_key: str, body: TurnInput, request: Request):
    tenant = widget_tenant(public_key, request)
    try:
        with Session.begin() as db:
            existing = find_turn(db, tenant.id, body.request_id)
            if existing:
                return replay(existing, body)
            conversation_id = str(body.conversation_id) if body.conversation_id else uid()
            if body.conversation_id:
                conversation = db.scalar(select(Conversation).where(
                    Conversation.id == conversation_id, Conversation.tenant_id == tenant.id,
                    Conversation.channel == 'web',
                ).with_for_update())
                if conversation is None:
                    raise HTTPException(404, 'Conversation not found')
                if db.scalar(select(WidgetTurn.id).where(
                    WidgetTurn.conversation_id == conversation_id, WidgetTurn.state.in_(('queued', 'processing')),
                ).limit(1)):
                    existing = find_turn(db, tenant.id, body.request_id)
                    if existing:
                        return replay(existing, body)
                    raise HTTPException(409, 'Wait for the previous message to finish')
            client = request.client.host if request.client else 'unknown'
            if not rag.chat_limiter.allow(f'web:{tenant.id}:{client}'):
                raise HTTPException(429, 'Too many messages. Please wait a minute.')
            if not body.conversation_id:
                db.add(Conversation(id=conversation_id, tenant_id=tenant.id, channel='web'))
                db.flush()
            turn = WidgetTurn(id=str(body.request_id), conversation_id=conversation_id,
                              message=body.message, handoff=body.handoff)
            db.add(turn)
            db.flush()
            return view(turn)
    except IntegrityError:
        with Session() as db:
            existing = find_turn(db, tenant.id, body.request_id)
            if existing:
                return replay(existing, body)
        raise HTTPException(409, 'Request ID is unavailable') from None


@router.get(BASE + '/turns/{turn_id}')
def read_turn(public_key: str, turn_id: UUID, request: Request):
    return turn_status(widget_tenant(public_key, request).id, turn_id)


@router.get(BASE + '/turns/{turn_id}/events')
async def turn_events(public_key: str, turn_id: UUID, request: Request):
    tenant = await run_in_threadpool(widget_tenant, public_key, request)
    initial = await run_in_threadpool(turn_status, tenant.id, turn_id)

    async def stream():
        current = initial
        for _ in range(30):
            if current['state'] in ('complete', 'failed'):
                yield 'event: result\ndata: ' + json.dumps(current) + '\n\n'
                return
            if await request.is_disconnected():
                return
            yield ': waiting\n\n'
            await asyncio.sleep(1)
            try:
                current = await run_in_threadpool(turn_status, tenant.id, turn_id)
            except HTTPException:
                return

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'})


@router.post(BASE + '/turns/{turn_id}/feedback')
def feedback(public_key: str, turn_id: UUID, body: Feedback, request: Request):
    tenant = widget_tenant(public_key, request)
    with Session.begin() as db:
        turn = find_turn(db, tenant.id, turn_id, lock=True)
        if turn is None or turn.state != 'complete':
            raise HTTPException(404, 'Completed widget message not found')
        turn.feedback = body.vote
        return {'feedback': turn.feedback}


@router.get(BASE + '/conversations/{conversation_id}/replies')
def agent_replies(public_key: str, conversation_id: UUID, request: Request, after: int = 0):
    tenant = widget_tenant(public_key, request)
    with Session() as db:
        rows = db.scalars(select(Message).join(Conversation, Conversation.id == Message.conversation_id).where(
            Conversation.id == str(conversation_id), Conversation.tenant_id == tenant.id,
            Conversation.channel == 'web', Message.role == 'agent', Message.kind != 'note', Message.id > after,
        ).order_by(Message.id).limit(100))
        return [{'id': row.id, 'text': row.content} for row in rows]
