"""Workspace-owned Razorpay accounts, approved dues and verified webhook inbox."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class PaymentConnection(Base):
    __tablename__ = 'payment_connections'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    label: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(8))
    key_last4: Mapped[str] = mapped_column(String(4))
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    webhook_secret_enc: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(24), default='needs_test')
    receipt_template_id: Mapped[str | None] = mapped_column(ForeignKey('templates.id', ondelete='SET NULL'))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PaymentRequest(Base):
    __tablename__ = 'payment_requests'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey('payment_connections.id', ondelete='CASCADE'))
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey('conversations.id', ondelete='SET NULL'), index=True)
    booking_id: Mapped[str | None] = mapped_column(ForeignKey('appointment_bookings.id', ondelete='SET NULL'))
    approval_key: Mapped[str] = mapped_column(String(80))
    amount: Mapped[int] = mapped_column(Integer)  # INR paise; never a floating-point amount.
    description: Mapped[str] = mapped_column(String(255))
    customer_name: Mapped[str] = mapped_column(String(100))
    customer_phone: Mapped[str] = mapped_column(String(16))
    language: Mapped[str] = mapped_column(String(16), default='en')
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(24), default='approved')
    provider_id: Mapped[str | None] = mapped_column(String(80))
    url: Mapped[str | None] = mapped_column(String(1024))
    payment_id: Mapped[str | None] = mapped_column(String(80))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    operation: Mapped[str | None] = mapped_column(String(16))
    job_state: Mapped[str] = mapped_column(String(16), default='complete', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    link_message_id: Mapped[int | None] = mapped_column(ForeignKey('messages.id', ondelete='SET NULL'))
    receipt_message_id: Mapped[int | None] = mapped_column(ForeignKey('messages.id', ondelete='SET NULL'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'approval_key', name='uq_payment_approval'),)


class PaymentEvent(Base):
    __tablename__ = 'payment_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey('payment_connections.id', ondelete='CASCADE'))
    event_key: Mapped[str] = mapped_column(String(64))
    payload_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    metadata_json: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(16), default='queued', index=True)
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('connection_id', 'event_key', name='uq_payment_event'),)
