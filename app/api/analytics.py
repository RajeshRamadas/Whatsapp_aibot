"""Aggregated workspace analytics; no customer identifiers or content leave this API."""
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Date, func, select

from ..db import AnalyticsDaily, ProductEvent, Session, Tenant, now
from .principal import tenant_for_role

router = APIRouter(prefix='/api/v1/analytics', tags=['analytics'])


class Metrics(BaseModel):
    conversations: int
    messages: int
    bot_answered: int
    handed_off: int
    leads: int
    bot_share: float
    handoff_rate: float
    first_response_seconds: float | None


class DailyMetrics(Metrics):
    day: date
    channel: str


class AnalyticsOverview(BaseModel):
    from_date: date
    to_date: date
    updated_at: datetime | None
    summary: Metrics
    daily: list[DailyMetrics]
    milestones: dict[str, int]


def metrics(rows):
    result = {field: sum(getattr(row, field) for row in rows) for field in (
        'conversations', 'messages', 'bot_answered', 'handed_off', 'leads', 'response_ms', 'response_count')}
    total = result['conversations']
    return Metrics(**{key: result[key] for key in ('conversations', 'messages', 'bot_answered', 'handed_off', 'leads')},
                   bot_share=100 * result['bot_answered'] / total if total else 0,
                   handoff_rate=100 * result['handed_off'] / total if total else 0,
                   first_response_seconds=result['response_ms'] / result['response_count'] / 1000
                   if result['response_count'] else None)


@router.get('', response_model=AnalyticsOverview)
def overview(from_date: date | None = None, to_date: date | None = None,
             channel: Literal['web', 'whatsapp'] | None = None,
             tenant: Tenant = Depends(tenant_for_role('viewer'))):
    end = to_date or now().date()
    start = from_date or date.fromordinal(max(1, end.toordinal() - 29))
    if end < start or (end - start).days > 89:
        raise HTTPException(422, 'Choose an inclusive date range of 1–90 days')
    with Session() as db:
        query = select(AnalyticsDaily).where(AnalyticsDaily.tenant_id == tenant.id,
                                            AnalyticsDaily.day >= start, AnalyticsDaily.day <= end)
        if channel:
            query = query.where(AnalyticsDaily.channel == channel)
        rows = db.scalars(query.order_by(AnalyticsDaily.day, AnalyticsDaily.channel)).all()
        events = db.execute(select(ProductEvent.name, func.count()).where(
            ProductEvent.tenant_id == tenant.id,
            func.timezone('UTC', ProductEvent.occurred_at).cast(Date) >= start,
            func.timezone('UTC', ProductEvent.occurred_at).cast(Date) <= end,
        ).group_by(ProductEvent.name)).all()
    return AnalyticsOverview(from_date=start, to_date=end,
                             updated_at=max((row.updated_at for row in rows), default=None), summary=metrics(rows),
                             daily=[DailyMetrics(day=row.day, channel=row.channel, **metrics([row]).model_dump())
                                    for row in rows], milestones=dict(events))
