"""Encrypted store connections and worker leases for atomic catalogue publication."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class StoreConnection(Base):
    __tablename__ = 'store_connections'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(100))
    origin: Mapped[str] = mapped_column(String(255))
    credentials_enc: Mapped[bytes] = mapped_column(LargeBinary)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default='needs_test')
    source_id: Mapped[str | None] = mapped_column(ForeignKey('sources.id', ondelete='SET NULL'), index=True)
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    next_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
