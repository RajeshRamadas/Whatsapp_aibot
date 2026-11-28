"""Workspace policy for a single automatic WhatsApp follow-up per lead."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now

FOLLOW_UP_REPLY_KEY = 'lead-follow-up'


class LeadFollowUpRule(Base):
    __tablename__ = 'lead_follow_up_rules'

    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True)
    template_id: Mapped[str | None] = mapped_column(ForeignKey('templates.id', ondelete='SET NULL'))
    enabled: Mapped[bool] = mapped_column(default=False)
    delay_hours: Mapped[int] = mapped_column(Integer, default=24)
    values: Mapped[list] = mapped_column(JSONB, default=list)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    configured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
