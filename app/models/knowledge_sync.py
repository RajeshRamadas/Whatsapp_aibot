"""Workspace-owned read-only knowledge connections and durable worker leases."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class KnowledgeConnection(Base):
    __tablename__ = 'knowledge_connections'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    provider: Mapped[str] = mapped_column(String(12))
    label: Mapped[str] = mapped_column(String(100))
    selection: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default='connected')
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    oauth_state_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    oauth_session_hash: Mapped[str | None] = mapped_column(String(64))
    oauth_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    oauth_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oauth_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    cursor: Mapped[str | None] = mapped_column(String(2048))
    lease: Mapped[str | None] = mapped_column(String(36))
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'provider', name='uq_knowledge_provider'),)
