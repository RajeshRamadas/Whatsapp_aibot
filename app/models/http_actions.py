"""Versioned action definitions and encrypted, conversation-bound execution records."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class HttpAction(Base):
    __tablename__ = 'http_actions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(32))
    definition: Mapped[dict] = mapped_column(JSONB)
    credentials_enc: Mapped[bytes] = mapped_column(LargeBinary)
    credential_last4: Mapped[str] = mapped_column(String(4))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    tested_revision: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default='needs_test')
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'name', name='uq_http_action_name'),)


class HttpActionCall(Base):
    __tablename__ = 'http_action_calls'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    action_id: Mapped[str] = mapped_column(ForeignKey('http_actions.id', ondelete='CASCADE'), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    dedupe_key: Mapped[str] = mapped_column(String(64))
    inputs_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    result_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    state: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    code_hash: Mapped[str | None] = mapped_column(String(64))
    code_attempts: Mapped[int] = mapped_column(Integer, default=0)
    otp_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('tenant_id', 'dedupe_key', name='uq_http_action_call'),
        Index('ix_http_action_calls_expiring', expires_at,
              postgresql_where=or_(inputs_enc.is_not(None), result_enc.is_not(None), code_hash.is_not(None))))
