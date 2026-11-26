"""Short-lived, session-bound Meta authorization; credentials are encrypted."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class MetaAuthorization(Base):
    __tablename__ = 'meta_authorizations'
    __table_args__ = (Index('ix_meta_authorizations_work', 'status', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    session_hash: Mapped[str] = mapped_column(ForeignKey('user_sessions.id_hash', ondelete='CASCADE'))
    app_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))
    state_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(24), default='authorizing')
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    selected_id: Mapped[str | None] = mapped_column(String(32))
    channel_id: Mapped[str | None] = mapped_column(ForeignKey('channels.id', ondelete='SET NULL'))
    claim_token: Mapped[str | None] = mapped_column(String(36))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
