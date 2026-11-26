"""Durable generation turns; inbound content stays in its existing message row."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class MetaBotTurn(Base):
    __tablename__ = 'meta_bot_turns'
    __table_args__ = (Index('ix_meta_bot_turns_work', 'status', 'incoming_message_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey('channels.id', ondelete='CASCADE'))
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'))
    incoming_message_id: Mapped[int] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), unique=True)
    status: Mapped[str] = mapped_column(String(16), default='queued')
    claim_token: Mapped[str | None] = mapped_column(String(36))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
