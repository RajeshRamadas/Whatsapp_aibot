"""Owners/admins register destinations and inspect metadata-only delivery requests."""
import secrets
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import Session, Tenant, now, uid
from ..models.webhooks import WebhookDelivery, WebhookEndpoint
from ..services import crypto, web_fetch
from ..services.audit import audit
from ..services.outgoing_webhooks import EventType
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/webhook-endpoints', tags=['outgoing-webhooks'])
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class EndpointInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Name
    url: str = Field(max_length=2048)
    events: list[EventType] = Field(min_length=1, max_length=9)

    @field_validator('url')
    @classmethod
    def safe_url(cls, value):
        if '#' in value:
            raise ValueError('Use an HTTPS endpoint without a fragment')
        return web_fetch.canonical(value)

    @field_validator('events')
    @classmethod
    def unique_events(cls, values):
        if len(values) != len(set(values)):
            raise ValueError('Events must be unique')
        return values


class EndpointUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    confirm_sharing: Literal[True] | None = None

    @model_validator(mode='after')
    def consent(self):
        if self.enabled and self.confirm_sharing is not True:
            raise ValueError('Confirm sharing event metadata before enabling this endpoint')
        return self


class EndpointView(BaseModel):
    id: str
    name: str
    url: str
    events: list[EventType]
    secret_last4: str
    enabled: bool
    disabled_reason: str | None
    failure_since: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    created_at: datetime


class CreatedEndpoint(EndpointView):
    secret: str


class DeliveryView(BaseModel):
    id: str
    endpoint_id: str
    state: str
    attempts: int
    response_code: int | None
    last_error: str | None
    created_at: datetime
    expires_at: datetime
    next_attempt_at: datetime
    payload: dict
    history: list[dict]


class DeliveryPage(BaseModel):
    items: list[DeliveryView]
    next_cursor: str | None


class Resend(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: UUID
    confirm: Literal[True]


def owned(db, model, tenant_id, identity, lock=False):
    query = select(model).where(model.tenant_id == tenant_id, model.id == str(identity))
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise HTTPException(404, 'Destination or delivery not found')
    return row


def view(row, schema):
    return {key: getattr(row, key) for key in schema.model_fields}


@router.post('', response_model=CreatedEndpoint, status_code=201)
def create(body: EndpointInput, response: Response, p: Principal = Depends(require_role('admin'))):
    secret = 'whsec_' + secrets.token_urlsafe(32)
    packet = crypto.encrypt(p.tenant.id, secret)
    try:
        with Session.begin() as db:
            db.get(Tenant, p.tenant.id, with_for_update=True)
            count = db.scalar(select(func.count()).select_from(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == p.tenant.id))
            if count >= settings.outgoing_webhook_max_endpoints:
                raise HTTPException(409, 'Destination limit reached; delete an unused destination first')
            row = WebhookEndpoint(tenant_id=p.tenant.id, name=body.name, url=body.url, events=body.events,
                                  secret_enc=packet, secret_last4=secret[-4:])
            db.add(row)
            db.flush()
            result = view(row, EndpointView)
            audit(db, p.tenant.id, 'webhook.created', row.id, actor_user_id=p.user.id if p.user else None)
    except IntegrityError:
        raise HTTPException(409, 'This destination is already registered') from None
    response.headers['Cache-Control'] = 'no-store'
    return result | {'secret': secret}


@router.get('', response_model=list[EndpointView])
def listing(p: Principal = Depends(require_role('admin'))):
    with Session() as db:
        return [view(row, EndpointView) for row in db.scalars(select(WebhookEndpoint).where(
            WebhookEndpoint.tenant_id == p.tenant.id).order_by(WebhookEndpoint.created_at, WebhookEndpoint.id))]


@router.patch('/{identity}', response_model=EndpointView)
def enable(identity: UUID, body: EndpointUpdate, p: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = owned(db, WebhookEndpoint, p.tenant.id, identity, lock=True)
        row.enabled, row.disabled_reason = body.enabled, None if body.enabled else 'paused'
        row.failure_since, row.last_error, row.notification_pending = None, None, False
        if not body.enabled:
            db.execute(update(WebhookDelivery).where(WebhookDelivery.tenant_id == p.tenant.id,
                WebhookDelivery.endpoint_id == row.id, WebhookDelivery.state.in_(['queued', 'sending']))
                .values(state='cancelled', last_error='endpoint_paused'))
        audit(db, p.tenant.id, 'webhook.enabled' if body.enabled else 'webhook.paused', row.id,
              actor_user_id=p.user.id if p.user else None)
        return view(row, EndpointView)


@router.delete('/{identity}', status_code=204)
def remove(identity: UUID, p: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = owned(db, WebhookEndpoint, p.tenant.id, identity, lock=True)
        audit(db, p.tenant.id, 'webhook.deleted', row.id, actor_user_id=p.user.id if p.user else None)
        db.delete(row)


@router.get('/{identity}/deliveries', response_model=DeliveryPage)
def deliveries(identity: UUID, limit: int = Query(30, ge=1, le=100), cursor: UUID | None = None,
               p: Principal = Depends(require_role('admin'))):
    with Session() as db:
        owned(db, WebhookEndpoint, p.tenant.id, identity)
        query = select(WebhookDelivery).where(WebhookDelivery.tenant_id == p.tenant.id,
            WebhookDelivery.endpoint_id == str(identity), WebhookDelivery.created_at >= now() - timedelta(days=30))
        if cursor:
            previous = owned(db, WebhookDelivery, p.tenant.id, cursor)
            if previous.endpoint_id != str(identity):
                raise HTTPException(422, 'Cursor belongs to another destination')
            query = query.where((WebhookDelivery.created_at < previous.created_at) | (
                (WebhookDelivery.created_at == previous.created_at) & (WebhookDelivery.id < previous.id)))
        rows = db.scalars(query.order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
                          .limit(limit + 1)).all()
        return {'items': [view(row, DeliveryView) for row in rows[:limit]],
                'next_cursor': rows[limit - 1].id if len(rows) > limit else None}


@router.post('/{identity}/deliveries/{delivery_id}/resend', response_model=DeliveryView, status_code=202)
def resend(identity: UUID, delivery_id: UUID, body: Resend, p: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        endpoint = owned(db, WebhookEndpoint, p.tenant.id, identity, lock=True)
        original = owned(db, WebhookDelivery, p.tenant.id, delivery_id)
        if original.endpoint_id != str(identity) or original.created_at < now() - timedelta(days=30):
            raise HTTPException(404, 'Delivery not found')
        if not endpoint.enabled:
            raise HTTPException(409, 'Enable this destination before resending')
        event_key = 'resend:' + str(body.request_key)
        previous = db.scalar(select(WebhookDelivery).where(WebhookDelivery.tenant_id == p.tenant.id,
            WebhookDelivery.endpoint_id == endpoint.id, WebhookDelivery.event_key == event_key))
        if previous:
            if previous.payload != original.payload:
                raise HTTPException(409, 'Request key already used for another event')
            return view(previous, DeliveryView)
        row = WebhookDelivery(id=uid(), tenant_id=p.tenant.id, endpoint_id=endpoint.id,
            event_key=event_key, payload=original.payload, expires_at=now() + timedelta(hours=24))
        db.add(row)
        db.flush()
        audit(db, p.tenant.id, 'webhook.resent', row.id, actor_user_id=p.user.id if p.user else None)
        return view(row, DeliveryView)
