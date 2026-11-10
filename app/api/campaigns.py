"""Workspace campaign preparation, explicit confirmation, scheduling and reports."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select, update

from ..db import Campaign, CampaignRecipient, Contact, Session, now
from ..services import campaigns as service
from ..services.audit import audit
from ..services.contacts import marketing_eligible
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/campaigns', tags=['campaigns'])


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: Literal['name', 'external_user', 'attribute', 'literal']
    value: str | None = Field(None, min_length=1, max_length=300)


class CampaignInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    template_id: UUID
    segment_id: UUID
    variables: list[Binding] = Field(default_factory=list, max_length=20)
    unit_price: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=6)
    currency: Literal['INR', 'USD'] = 'INR'


class TestInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    contact_id: UUID
    own_number: Literal[True]


class StartInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: Literal[True]
    scheduled_at: datetime | None = None


class Report(BaseModel):
    id: str
    name: str
    state: str
    reason: str | None
    scheduled_at: datetime | None
    recipients: int
    excluded: int
    currency: str
    estimated_max_cost: Decimal | None
    estimated_billable_cost: Decimal | None
    pricing_unconfirmed: int
    test_accepted: bool
    results: dict[str, int]
    preview: list[dict[str, str]]


class RecipientResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    contact_id: str
    is_test: bool
    state: str
    attempts: int
    error_code: str | None
    provider_id: str | None
    replied_at: datetime | None
    opted_out_at: datetime | None
    billable: bool | None


@router.post('', response_model=Report, status_code=201)
def create(body: CampaignInput, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = service.prepare(db, principal.tenant.id, body, principal.user.id if principal.user else None)
        return service.report(db, row)


@router.get('', response_model=list[Report])
def listing(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
            principal: Principal = Depends(require_role('admin'))):
    with Session() as db:
        rows = db.scalars(select(Campaign).where(Campaign.tenant_id == principal.tenant.id)
            .order_by(Campaign.created_at.desc(), Campaign.id).offset(offset).limit(limit)).all()
        return [service.report(db, row) for row in rows]


@router.get('/{campaign_id}', response_model=Report)
def details(campaign_id: UUID, principal: Principal = Depends(require_role('admin'))):
    with Session() as db:
        return service.report(db, service.scoped(db, Campaign, campaign_id, principal.tenant.id))


@router.get('/{campaign_id}/recipients', response_model=list[RecipientResult])
def recipients(campaign_id: UUID, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               principal: Principal = Depends(require_role('admin'))):
    with Session() as db:
        service.scoped(db, Campaign, campaign_id, principal.tenant.id)
        rows = db.scalars(select(CampaignRecipient).where(CampaignRecipient.tenant_id == principal.tenant.id,
            CampaignRecipient.campaign_id == str(campaign_id)).order_by(CampaignRecipient.id)
            .offset(offset).limit(limit)).all()
        return [{key: getattr(row, key) for key in ('id', 'contact_id', 'is_test', 'state', 'attempts',
                 'error_code', 'provider_id', 'replied_at', 'opted_out_at', 'billable')} for row in rows]


@router.post('/{campaign_id}/test', status_code=202)
def test(campaign_id: UUID, body: TestInput, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = service.scoped(db, Campaign, campaign_id, principal.tenant.id, lock=True)
        if row.state != 'draft':
            raise HTTPException(409, 'Only drafts accept test sends')
        contact = service.scoped(db, Contact, body.contact_id, principal.tenant.id)
        if not marketing_eligible(contact):
            raise HTTPException(409, 'The test recipient must have opted in')
        service.current_template(db, row)
        existing = db.scalar(select(CampaignRecipient).where(CampaignRecipient.tenant_id == principal.tenant.id,
            CampaignRecipient.campaign_id == row.id, CampaignRecipient.is_test.is_(True)))
        if existing:
            if existing.contact_id != contact.id:
                raise HTTPException(409, 'A test is already recorded for this campaign')
            return {'id': existing.id, 'state': existing.state}
        recipient = CampaignRecipient(tenant_id=principal.tenant.id, campaign_id=row.id, contact_id=contact.id,
                                      is_test=True, values=service.personalize(row.bindings, contact))
        db.add(recipient)
        db.flush()
        return {'id': recipient.id, 'state': recipient.state}


@router.post('/{campaign_id}/start', response_model=Report)
def start(campaign_id: UUID, body: StartInput, principal: Principal = Depends(require_role('admin'))):
    if body.scheduled_at and (body.scheduled_at.tzinfo is None or body.scheduled_at < now()):
        raise HTTPException(422, 'Schedule a future time with a timezone')
    with Session.begin() as db:
        row = service.scoped(db, Campaign, campaign_id, principal.tenant.id, lock=True)
        if row.state in ('scheduled', 'sending'):
            return service.report(db, row)
        if row.state not in ('draft', 'paused') or not service.report(db, row)['test_accepted']:
            raise HTTPException(409, 'Complete the own-number test before confirming this campaign')
        service.current_template(db, row)
        row.state, row.reason, row.scheduled_at = 'scheduled', None, body.scheduled_at or now()
        audit(db, principal.tenant.id, 'campaign.confirmed', row.id,
              actor_user_id=principal.user.id if principal.user else None)
        return service.report(db, row)


@router.post('/{campaign_id}/cancel', response_model=Report)
def cancel(campaign_id: UUID, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = service.scoped(db, Campaign, campaign_id, principal.tenant.id, lock=True)
        if row.state != 'completed':
            row.state = 'cancelled'
            db.execute(update(CampaignRecipient).where(CampaignRecipient.tenant_id == principal.tenant.id,
                CampaignRecipient.campaign_id == row.id, CampaignRecipient.state == 'queued').values(state='cancelled'))
        return service.report(db, row)
