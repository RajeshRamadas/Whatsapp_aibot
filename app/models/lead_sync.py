"""Explicit lead-sharing connections and an immutable, tenant-scoped delivery outbox."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class LeadConnection(Base):
    __tablename__ = 'lead_connections'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(8), default='in')
    status: Mapped[str] = mapped_column(String(24), default='needs_test')
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_last4: Mapped[str | None] = mapped_column(String(4))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    catalog: Mapped[dict] = mapped_column(JSONB, default=dict)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oauth_state_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    oauth_session_hash: Mapped[str | None] = mapped_column(String(64))
    oauth_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    oauth_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oauth_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LeadDelivery(Base):
    __tablename__ = 'lead_deliveries'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey('lead_connections.id', ondelete='CASCADE'), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey('conversations.id', ondelete='SET NULL'), index=True)
    kind: Mapped[str] = mapped_column(String(16), default='sync')
    lead_version: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(16), default='queued', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    note_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    contact_done: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_id: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('connection_id', 'conversation_id', 'lead_version', 'revision',
                                      name='uq_lead_delivery_version'),)
