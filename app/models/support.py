"""A short-lived workspace grant bound to the operator's normal session."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, uid


class SupportSession(Base):
    __tablename__ = 'support_sessions'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_session_id: Mapped[str | None] = mapped_column(
        ForeignKey('user_sessions.id_hash', ondelete='SET NULL'), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
