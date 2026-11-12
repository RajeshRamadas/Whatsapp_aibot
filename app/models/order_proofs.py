"""Short-lived encrypted order snapshots, bound to one conversation and request."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, uid


class OrderProof(Base):
    __tablename__ = 'order_proofs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey('commerce_stores.id', ondelete='CASCADE'))
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'))
    dedupe_key: Mapped[str] = mapped_column(String(64))
    payload_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    code_hash: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (UniqueConstraint('tenant_id', 'dedupe_key', name='uq_order_proof_turn'),)
