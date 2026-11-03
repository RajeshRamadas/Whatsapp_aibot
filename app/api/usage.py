"""Workspace usage and owner quota policy."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from ..db import Plan, Session, Tenant, UsageDaily, now
from ..services.usage import month_start, monthly_count
from .principal import tenant_for_role

router = APIRouter(prefix='/api/v1/usage')


class QuotaPolicy(BaseModel):
    behavior: Literal['pause', 'overage']


@router.get('')
def summary(days: int = Query(30, ge=1, le=90), tenant: Tenant = Depends(tenant_for_role('owner'))):
    start = month_start(now())
    with Session() as db:
        plan = db.get(Plan, tenant.plan_id)
        count = monthly_count(db, tenant.id, start)
        rows = db.scalars(select(UsageDaily).where(UsageDaily.tenant_id == tenant.id)
                          .order_by(UsageDaily.day.desc()).limit(days)).all()
    return {'plan': plan.id, 'limits': plan.limits, 'month': start.date(), 'ai_messages': count,
            'quota_behavior': tenant.quota_behavior, 'daily': [
                {'day': row.day, 'ai_messages': row.ai_messages, 'overage_messages': row.overage_messages,
                 'ai_calls': row.ai_calls, 'input_tokens': row.input_tokens, 'output_tokens': row.output_tokens,
                 'cost_usd': str(row.cost_usd), 'unpriced_calls': row.unpriced_calls} for row in rows
            ]}


@router.put('/quota-policy')
def change_policy(body: QuotaPolicy, tenant: Tenant = Depends(tenant_for_role('owner'))):
    with Session.begin() as db:
        db.get(Tenant, tenant.id, with_for_update=True).quota_behavior = body.behavior
    return body
