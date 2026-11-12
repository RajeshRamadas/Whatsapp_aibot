"""One read-only store per workspace; credentials never leave the backend."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class Store(Base):
    __tablename__ = 'commerce_stores'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), unique=True)
    provider: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(255))
    credentials_enc: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(20), default='needs_test')
    source_id: Mapped[str | None] = mapped_column(ForeignKey('sources.id', ondelete='SET NULL'))
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease: Mapped[str | None] = mapped_column(String(36))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
