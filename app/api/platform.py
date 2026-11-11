"""Cross-workspace operations require an explicit platform-admin user session."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import case, func, literal, select, union_all

from ..db import (
    AnswerReview,
    Conversation,
    EmailOutbox,
    InboundEvent,
    Session,
    Source,
    Tenant,
    UsageEvent,
    User,
    WebhookFailure,
    Website,
    WidgetTurn,
    now,
)
from ..models.google_calendar import GoogleTask
from ..services.audit import audit
from ..services.usage import month_start
from .auth import _csrf, _session


def platform_admin(request: Request) -> User:
    row, user = _session(request)
    if not user.is_platform_admin:
        raise HTTPException(403, 'Platform administrator required')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        _csrf(request, row)
    return user


router = APIRouter(prefix='/api/v1/platform', tags=['platform'], dependencies=[Depends(platform_admin)])


class PlatformTenant(BaseModel):
    id: str
    name: str
    plan: str
    suspended: bool
    bot_live: bool
    ai_messages: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    unpriced_calls: int


class PlatformTenants(BaseModel):
    items: list[PlatformTenant]
    total: int
    limit: int
    offset: int
    month: datetime


class FailedJob(BaseModel):
    id: str
    tenant_id: str | None
    kind: Literal['ingestion', 'whatsapp', 'widget', 'email', 'website', 'answer_review']
    received_at: datetime


class FailedJobs(BaseModel):
    items: list[FailedJob]
    total: int
    limit: int
    offset: int


class FailedWebhook(BaseModel):
    id: str
    provider: str
    http_status: int
    received_at: datetime


class FailedWebhooks(BaseModel):
    items: list[FailedWebhook]
    total: int
    limit: int
    offset: int


class Suspension(BaseModel):
    model_config = ConfigDict(extra='forbid')
    suspended: StrictBool


@router.get('/me')
def me():
    return {'is_platform_admin': True}


@router.get('/tenants', response_model=PlatformTenants)
def tenants(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
            search: str = Query('', max_length=200)):
    start = month_start(now())
    calls = UsageEvent.kind.in_(('chat', 'embedding'))
    totals = select(
        UsageEvent.tenant_id,
        func.sum(case((UsageEvent.kind == 'answer', 1), else_=0)).label('ai_messages'),
        func.sum(UsageEvent.input_tokens).label('input_tokens'),
        func.sum(UsageEvent.output_tokens).label('output_tokens'),
        func.sum(UsageEvent.cost_usd).label('cost_usd'),
        func.sum(case((calls & UsageEvent.cost_usd.is_(None), 1), else_=0)).label('unpriced_calls'),
    ).where(UsageEvent.created_at >= start).group_by(UsageEvent.tenant_id).subquery()
    # Global reads are confined to this router, after platform_admin has authorized the user.
    match = Tenant.name.icontains(search, autoescape=True)
    with Session() as db:
        total = db.scalar(select(func.count()).select_from(Tenant).where(match))
        rows = db.execute(select(Tenant, totals).outerjoin(totals, totals.c.tenant_id == Tenant.id)
                          .where(match).order_by(Tenant.name, Tenant.id).offset(offset).limit(limit)).all()
        items = [PlatformTenant(id=row.Tenant.id, name=row.Tenant.name, plan=row.Tenant.plan_id,
                                suspended=row.Tenant.suspended, bot_live=row.Tenant.bot_live,
                                **{key: getattr(row, key) or 0 for key in (
                                    'ai_messages', 'input_tokens', 'output_tokens', 'cost_usd', 'unpriced_calls')})
                 for row in rows]
    return PlatformTenants(items=items, total=total, limit=limit, offset=offset, month=start)


@router.put('/tenants/{tenant_id}/suspension', response_model=Suspension)
def suspend(tenant_id: UUID, body: Suspension, user: User = Depends(platform_admin)):
    with Session.begin() as db:
        tenant = db.get(Tenant, str(tenant_id), with_for_update=True)
        if tenant is None:
            raise HTTPException(404, 'Workspace not found')
        if tenant.suspended != body.suspended:
            tenant.suspended = body.suspended
            audit(db, tenant.id, 'tenant.suspended' if body.suspended else 'tenant.unsuspended', tenant.id,
                  actor_user_id=user.id, actor_type='platform_admin')
    return body


@router.get('/failed-jobs', response_model=FailedJobs)
def failed_jobs(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    # Terminal records survive Redis job-result expiry. Deliberately omit raw errors and payloads.
    failures = union_all(
        select(GoogleTask.id, GoogleTask.tenant_id, literal('google_calendar').label('kind'),
               GoogleTask.created_at.label('received_at')).where(GoogleTask.state == 'failed'),
        select(Source.id, Source.tenant_id, literal('ingestion').label('kind'),
               Source.created_at.label('received_at')).where(Source.status == 'failed'),
        select(InboundEvent.id, InboundEvent.tenant_id, literal('whatsapp'), InboundEvent.received_at)
        .where(InboundEvent.status == 'failed'),
        select(WidgetTurn.id, Conversation.tenant_id, literal('widget'), WidgetTurn.created_at)
        .join(Conversation, Conversation.id == WidgetTurn.conversation_id).where(WidgetTurn.state == 'failed'),
        select(AnswerReview.id, AnswerReview.tenant_id, literal('answer_review'), AnswerReview.created_at)
        .where(AnswerReview.state == 'failed'),
        select(Website.id, Website.tenant_id, literal('website'), Website.created_at)
        .where(Website.status == 'failed'),
        select(EmailOutbox.id, literal(None), literal('email'), EmailOutbox.created_at)
        .where(EmailOutbox.status == 'failed'),
    ).subquery()
    with Session() as db:
        total = db.scalar(select(func.count()).select_from(failures))
        rows = db.execute(select(failures).order_by(failures.c.received_at.desc(), failures.c.id,
                                                   failures.c.kind).offset(offset).limit(limit)).mappings().all()
    return FailedJobs(items=[FailedJob(**row) for row in rows], total=total, limit=limit, offset=offset)


@router.get('/webhook-failures', response_model=FailedWebhooks)
def webhook_failures(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    with Session() as db:
        total = db.scalar(select(func.count()).select_from(WebhookFailure))
        rows = db.scalars(select(WebhookFailure).order_by(WebhookFailure.received_at.desc(), WebhookFailure.id)
                          .offset(offset).limit(limit)).all()
    return FailedWebhooks(items=[FailedWebhook.model_validate(row, from_attributes=True) for row in rows],
                          total=total, limit=limit, offset=offset)
