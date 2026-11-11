"""Immutable inputs and durable progress for tenant accuracy runs."""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class EvalRun(Base):
    __tablename__ = 'eval_runs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    request_key: Mapped[str] = mapped_column(String(36))
    label: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(16), default='queued', index=True)
    cases: Mapped[list] = mapped_column(JSONB)
    context: Mapped[dict] = mapped_column(JSONB)
    results: Mapped[list] = mapped_column(JSONB, default=list)
    total: Mapped[int] = mapped_column(Integer)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    accuracy: Mapped[float | None] = mapped_column(Float)
    source_hit_rate: Mapped[float | None] = mapped_column(Float)
    last_error: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'request_key', name='uq_eval_run_request'),)
