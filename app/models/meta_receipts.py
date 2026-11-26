"""Durable metadata-only Meta receipts, bound to a verified asset."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class MetaReceipt(Base):
    __tablename__ = 'meta_receipts'
    __table_args__ = (UniqueConstraint('channel_id', 'dedupe_key', name='uq_meta_receipt'),
                     Index('ix_meta_receipts_ready', 'status', 'next_attempt_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey('channels.id', ondelete='CASCADE'))
    sender_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    mids: Mapped[list] = mapped_column(JSONB, default=list)
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default='queued')
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
