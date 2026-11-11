"""Merchant payment links use workspace keys, distinct from platform subscription billing."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, StrictBool, StringConstraints
from sqlalchemy import select

from ..db import Session
from ..models.payments import PaymentConnection
from ..services import payments
from ..services.appointments import AppointmentError
from .appointments import Confirm
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/payments', tags=['payments'])
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class MerchantInput(Input):
    label: Name
    key_id: str = Field(pattern=r'^rzp_(test|live)_[a-zA-Z0-9]{4,80}$')
    key_secret: SecretStr = Field(min_length=8, max_length=200)
    webhook_secret: SecretStr = Field(min_length=16, max_length=200)
    confirm_live: StrictBool = False
    receipt_template_id: UUID | None = None


class MerchantView(BaseModel):
    id: str
    label: str
    mode: str
    key_last4: str
    status: str
    receipt_template_id: str | None
    last_success_at: datetime | None
    last_error: str | None


class PaymentApproval(Input):
    connection_id: UUID
    conversation_id: UUID
    booking_id: UUID | None = None
    approval_key: UUID
    amount: Annotated[int, Field(strict=True, ge=100, le=100000000)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    customer_name: Name
    customer_phone: str = Field(pattern=r'^\+?[1-9][0-9]{6,14}$')
    language: Literal['en', 'hi', 'hi-Latn', 'kn', 'ta', 'te', 'mr', 'bn'] | None = None
    expires_at: AwareDatetime


class PaymentView(BaseModel):
    id: str
    connection_id: str
    conversation_id: str | None
    booking_id: str | None
    amount: int
    description: str
    language: str
    expires_at: datetime
    state: str
    provider_id: str | None
    url: str | None
    payment_id: str | None
    job_state: str
    last_error: str | None
    link_delivery: str | None
    receipt_delivery: str | None


def run(principal, function, *args, **kwargs):
    try:
        return function(principal.tenant.id, *args, **kwargs)
    except AppointmentError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except ValueError:
        raise HTTPException(503, 'Credential encryption is unavailable') from None


def actor(p):
    return ('user', p.user.id) if p.user else ('api_key', None)


@router.get('/connections', response_model=list[MerchantView])
def connections(p: Principal = Depends(require_role('admin'))):
    return payments.connections(p.tenant.id)


@router.post('/connections', response_model=MerchantView, status_code=201)
def connect(body: MerchantInput, p: Principal = Depends(require_role('admin'))):
    values = body.model_dump(mode='json')
    values.update(key_secret=body.key_secret.get_secret_value(), webhook_secret=body.webhook_secret.get_secret_value())
    return run(p, payments.connect, values, actor=actor(p))


@router.post('/connections/{identity}/test', response_model=MerchantView)
def test(identity: UUID, p: Principal = Depends(require_role('admin'))):
    return run(p, payments.test_connection, str(identity), actor=actor(p))


@router.delete('/connections/{identity}', response_model=MerchantView)
def disconnect(identity: UUID, body: Confirm, p: Principal = Depends(require_role('admin'))):
    return run(p, payments.disconnect, str(identity), actor=actor(p))


@router.get('/requests', response_model=list[PaymentView])
def listing(conversation_id: UUID | None = None, p: Principal = Depends(require_role('agent'))):
    return run(p, payments.listing, str(conversation_id) if conversation_id else None)


@router.post('/requests', response_model=PaymentView, status_code=201)
def approve(body: PaymentApproval, p: Principal = Depends(require_role('agent'))):
    values = body.model_dump(mode='json')
    values['expires_at'] = body.expires_at
    return run(p, payments.approve, values, actor=actor(p))


@router.post('/requests/{identity}/{action}', response_model=PaymentView)
def change(identity: UUID, action: Literal['send', 'sync', 'cancel'], body: Confirm,
           p: Principal = Depends(require_role('agent'))):
    return run(p, payments.action, str(identity), action, actor=actor(p))


class MerchantOption(BaseModel):
    id: str
    label: str
    mode: str
    status: str


class PaymentCatalog(BaseModel):
    timezone: str
    connections: list[MerchantOption]


@router.get('/catalog', response_model=PaymentCatalog)
def catalog(p: Principal = Depends(require_role('agent'))):
    with Session() as db:
        rows = db.scalars(select(PaymentConnection).where(PaymentConnection.tenant_id == p.tenant.id,
            PaymentConnection.status != 'disconnected').order_by(PaymentConnection.created_at)).all()
        return {'timezone': p.tenant.timezone, 'connections': [payments.connection_view(c) for c in rows]}
