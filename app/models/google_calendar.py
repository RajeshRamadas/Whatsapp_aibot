"""Encrypted OAuth connections and a durable Google operation outbox."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class GoogleConnection(Base):
    __tablename__ = 'google_calendar_connections'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    label: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default='requested')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oauth_state_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    oauth_session_hash: Mapped[str | None] = mapped_column(String(64))
    oauth_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    oauth_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oauth_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    calendars: Mapped[list] = mapped_column(JSONB, default=list)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class GoogleTask(Base):
    __tablename__ = 'google_calendar_tasks'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    connection_id: Mapped[str | None] = mapped_column(ForeignKey('google_calendar_connections.id', ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String(24))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(16), default='queued', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
