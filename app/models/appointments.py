"""Product-managed appointment availability and durable five-minute holds."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class Service(Base):
    __tablename__ = 'appointment_services'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    reminder_minutes: Mapped[int] = mapped_column(Integer, default=60)
    reminder_template_id: Mapped[str | None] = mapped_column(ForeignKey('templates.id', ondelete='SET NULL'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Staff(Base):
    __tablename__ = 'appointment_staff'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Slot(Base):
    __tablename__ = 'appointment_slots'
    calendar_signature: Mapped[str | None] = mapped_column(String(64))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    service_id: Mapped[str] = mapped_column(ForeignKey('appointment_services.id', ondelete='CASCADE'))
    staff_id: Mapped[str] = mapped_column(ForeignKey('appointment_staff.id', ondelete='CASCADE'), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Booking(Base):
    __tablename__ = 'appointment_bookings'
    calendar_targets: Mapped[list] = mapped_column(JSONB, default=list)
    calendar_state: Mapped[str] = mapped_column(String(24), default='none')
    calendar_version: Mapped[int] = mapped_column(Integer, default=0)
    calendar_error: Mapped[str | None] = mapped_column(String(64))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    slot_id: Mapped[str] = mapped_column(ForeignKey('appointment_slots.id', ondelete='CASCADE'))
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    customer_name: Mapped[str] = mapped_column(String(100))
    customer_phone: Mapped[str] = mapped_column(String(16))
    language: Mapped[str] = mapped_column(String(16), default='en')
    state: Mapped[str] = mapped_column(String(16), default='held')
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    replaces_id: Mapped[str | None] = mapped_column(ForeignKey('appointment_bookings.id', ondelete='SET NULL'))
    reminder_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reminder_state: Mapped[str] = mapped_column(String(16), default='pending')
    reminder_error: Mapped[str | None] = mapped_column(String(40))
    reminder_message_id: Mapped[int | None] = mapped_column(ForeignKey('messages.id', ondelete='SET NULL'))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (Index('uq_appointment_slot_reserved', 'slot_id', unique=True,
                           postgresql_where=text("state IN ('held', 'confirming', 'confirmed')")),)
