"""Tenant-filtered resources with stable, opaque keyset pagination."""
from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select

from ..api.evals import CaseView, RunDetail
from ..api.leads import LeadField
from ..api.shared import EvalCaseInput
from ..db import Contact, Conversation, EvalCase, Lead, Message, QAPair, Session, Source
from ..models.evaluations import EvalRun
from ..services import evaluation_runs, leads
from ..services.contacts import Attributes, Language
from .auth import PublicPrincipal, require
from .contracts import PublicError, cursor, decode_cursor, post

router = APIRouter()
T = TypeVar('T')
Key = Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=2048)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None
    limit: int


class SourceView(BaseModel):
    id: str
    filename: str
    kind: str
    status: str
    created_at: datetime


class PairView(BaseModel):
    id: str
    question: str
    answer: str


class ContactView(BaseModel):
    id: str
    channel: str
    external_user: str
    name: str | None
    language: str | None
    attributes: dict
    opt_in_at: datetime | None
    opt_out_at: datetime | None


class ConversationView(BaseModel):
    id: str
    channel: str
    contact_id: str | None
    status: str
    handoff: bool
    labels: list[str]
    created_at: datetime
    last_inbound_at: datetime | None
    last_message_at: datetime | None


class LeadView(BaseModel):
    id: str
    conversation_id: str
    fields: dict
    status: str
    owner_id: str | None
    notes: str
    created_at: datetime


class MessageView(BaseModel):
    id: int
    conversation_id: str
    role: str
    direction: str
    kind: str
    content: str
    delivery_status: str | None
    created_at: datetime


def view(row, schema):
    if isinstance(row, EvalRun):
        return evaluation_runs.view(row, True)
    values = {key: getattr(row, key) for key in schema.model_fields if key != 'id' or not isinstance(row, Lead)}
    if isinstance(row, Lead):
        values['id'] = row.conversation_id
    return values


def owned(db, model, tenant_id, identity, lock=False):
    column = Lead.conversation_id if model is Lead else model.id
    query = select(model).where(model.tenant_id == tenant_id, column == str(identity))
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise PublicError(404, 'not_found', 'Resource not found')
    return row


def page(db, query, column, tenant_id, resource, schema, limit, token, filters=None, integer=False):
    if token:
        last = decode_cursor(token, tenant_id, resource, filters)
        try:
            last = int(last) if integer else str(UUID(str(last)))
        except (ValueError, TypeError):
            raise PublicError(422, 'invalid_cursor', 'Invalid pagination position') from None
        query = query.where(column > last)
    rows = db.scalars(query.order_by(column).limit(limit + 1)).all()
    next_token = (cursor(tenant_id, resource, getattr(rows[limit - 1], column.key), filters)
                  if len(rows) > limit else None)
    return {'items': [view(row, schema) for row in rows[:limit]], 'next_cursor': next_token, 'limit': limit}


def resource(path, model, schema, scope):
    column = Lead.conversation_id if model is Lead else model.id
    def listing(limit: Limit = 30, cursor: Cursor = None, p: PublicPrincipal = Depends(require(scope))):
        with Session() as db:
            return page(db, select(model).where(model.tenant_id == p.tenant.id), column,
                        p.tenant.id, path, schema, limit, cursor)
    def detail(identity: UUID, p: PublicPrincipal = Depends(require(scope))):
        with Session() as db:
            return view(owned(db, model, p.tenant.id, identity), schema)
    router.add_api_route('/' + path, listing, methods=['GET'], response_model=Page[schema],
                         tags=[path], operation_id='list_' + path.replace('-', '_'))
    router.add_api_route('/' + path + '/{identity}', detail, methods=['GET'], response_model=schema,
                         tags=[path], operation_id='get_' + path.replace('-', '_'))


for args in [('sources', Source, SourceView, 'knowledge:read'), ('qa-pairs', QAPair, PairView, 'knowledge:read'),
             ('contacts', Contact, ContactView, 'contacts:read'),
             ('conversations', Conversation, ConversationView, 'conversations:read'),
             ('leads', Lead, LeadView, 'leads:read'), ('eval-cases', EvalCase, CaseView, 'evals:read'),
             ('eval-runs', EvalRun, RunDetail, 'evals:read')]:
    resource(*args)


def messages_query(tenant_id):
    return select(Message).join(Conversation, Message.conversation_id == Conversation.id).where(
        Conversation.tenant_id == tenant_id)


@router.get('/messages', response_model=Page[MessageView], tags=['messages'])
def messages(limit: Limit = 30, cursor: Cursor = None, conversation_id: UUID | None = None,
             p: PublicPrincipal = Depends(require('conversations:read'))):
    with Session() as db:
        query = messages_query(p.tenant.id)
        if conversation_id:
            owned(db, Conversation, p.tenant.id, conversation_id)
            query = query.where(Message.conversation_id == str(conversation_id))
        return page(db, query, Message.id, p.tenant.id, 'messages', MessageView, limit, cursor,
                    str(conversation_id) if conversation_id else None, integer=True)


@router.get('/messages/{identity}', response_model=MessageView, tags=['messages'])
def message(identity: Annotated[int, Field(gt=0)], p: PublicPrincipal = Depends(require('conversations:read'))):
    with Session() as db:
        row = db.scalar(messages_query(p.tenant.id).where(Message.id == identity))
        if row is None:
            raise PublicError(404, 'not_found', 'Message not found')
        return view(row, MessageView)


class ContactInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    channel: Literal['whatsapp', 'web']
    external_user: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    name: Text | None = None
    language: Language | None = None
    attributes: Attributes = Field(default_factory=dict)

    @model_validator(mode='after')
    def phone(self):
        if self.channel == 'whatsapp':
            from ..services.contacts import ImportRow
            self.external_user = ImportRow(phone=self.external_user).phone.lstrip('+')
        return self


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    contact_id: UUID


class LeadInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: UUID
    fields: dict[LeadField, Annotated[str, StringConstraints(max_length=2000)]] = Field(min_length=1, max_length=7)


def created(row, schema, path):
    return 201, view(row, schema), {'Location': '/api/v1/public/' + path}


@router.post('/contacts', response_model=ContactView, status_code=201, tags=['contacts'])
def create_contact(body: ContactInput, key: Key, p: PublicPrincipal = Depends(require('contacts:write'))):
    def save(db, identity):
        row = Contact(id=identity, tenant_id=p.tenant.id, **body.model_dump())
        db.add(row)
        db.flush()
        return created(row, ContactView, 'contacts/' + row.id)
    return post(p, key, '/contacts', body.model_dump(mode='json'), save)


@router.post('/conversations', response_model=ConversationView, status_code=201, tags=['conversations'])
def create_conversation(body: ConversationInput, key: Key,
                        p: PublicPrincipal = Depends(require('conversations:write'))):
    def save(db, identity):
        contact = owned(db, Contact, p.tenant.id, body.contact_id)
        row = Conversation(id=identity, tenant_id=p.tenant.id, contact_id=contact.id,
                           channel=contact.channel, external_user=contact.external_user, status='agent', handoff=True)
        db.add(row)
        db.flush()
        return created(row, ConversationView, 'conversations/' + row.id)
    return post(p, key, '/conversations', body.model_dump(mode='json'), save)


@router.post('/leads', response_model=LeadView, status_code=201, tags=['leads'])
def capture_lead(body: LeadInput, key: Key, p: PublicPrincipal = Depends(require('leads:write'))):
    def save(db, identity):
        conv = owned(db, Conversation, p.tenant.id, body.conversation_id, lock=True)
        leads.save_lead(db, conv, body.fields)
        row = owned(db, Lead, p.tenant.id, conv.id)
        return created(row, LeadView, 'leads/' + conv.id)
    return post(p, key, '/leads', body.model_dump(mode='json'), save)


class CaseInput(EvalCaseInput):
    model_config = ConfigDict(extra='forbid')


@router.post('/eval-cases', response_model=CaseView, status_code=201, tags=['eval-cases'])
def create_case(body: CaseInput, key: Key, p: PublicPrincipal = Depends(require('evals:write'))):
    def save(db, identity):
        row = EvalCase(id=identity, tenant_id=p.tenant.id, **body.model_dump())
        db.add(row)
        db.flush()
        return created(row, CaseView, 'eval-cases/' + row.id)
    return post(p, key, '/eval-cases', body.model_dump(mode='json'), save)


@router.put('/eval-cases/{identity}', response_model=CaseView, tags=['eval-cases'])
def update_case(identity: UUID, body: CaseInput, p: PublicPrincipal = Depends(require('evals:write'))):
    with Session.begin() as db:
        row = owned(db, EvalCase, p.tenant.id, identity, lock=True)
        for name, value in body.model_dump().items():
            setattr(row, name, value)
        return view(row, CaseView)


@router.delete('/eval-cases/{identity}', status_code=204, tags=['eval-cases'])
def delete_case(identity: UUID, p: PublicPrincipal = Depends(require('evals:write'))):
    with Session.begin() as db:
        db.delete(owned(db, EvalCase, p.tenant.id, identity, lock=True))


class ContactPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Text | None = None
    language: Language | None = None
    attributes: Attributes = Field(default_factory=dict)


class ConversationPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['bot', 'agent', 'waiting', 'closed']
    labels: list[Annotated[str, StringConstraints(min_length=1, max_length=50)]] = Field(max_length=20)


class LeadPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['new', 'contacted', 'qualified', 'won', 'lost']
    notes: str = Field(max_length=8000)


@router.patch('/contacts/{identity}', response_model=ContactView, tags=['contacts'])
def update_contact(identity: UUID, body: ContactPatch, p: PublicPrincipal = Depends(require('contacts:write'))):
    with Session.begin() as db:
        row = owned(db, Contact, p.tenant.id, identity, lock=True)
        for name, value in body.model_dump(exclude_unset=True).items():
            setattr(row, name, value)
        return view(row, ContactView)


@router.patch('/conversations/{identity}', response_model=ConversationView, tags=['conversations'])
def update_conversation(identity: UUID, body: ConversationPatch,
                        p: PublicPrincipal = Depends(require('conversations:write'))):
    with Session.begin() as db:
        row = owned(db, Conversation, p.tenant.id, identity, lock=True)
        row.status, row.labels, row.handoff = body.status, body.labels, body.status != 'bot'
        return view(row, ConversationView)


@router.patch('/leads/{identity}', response_model=LeadView, tags=['leads'])
def update_lead(identity: UUID, body: LeadPatch, p: PublicPrincipal = Depends(require('leads:write'))):
    from ..services.lead_sync import changed
    with Session.begin() as db:
        row = owned(db, Lead, p.tenant.id, identity, lock=True)
        if (row.status, row.notes) != (body.status, body.notes):
            row.status, row.notes = body.status, body.notes
            db.flush()
            changed(db, p.tenant.id, str(identity))
        return view(row, LeadView)
