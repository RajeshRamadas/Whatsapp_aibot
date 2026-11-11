"""Metadata for typed tables in isolated tenant schemas."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, now, uid


class SpreadsheetTable(Base):
    __tablename__ = 'spreadsheet_tables'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey('sources.id', ondelete='CASCADE'), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    table_name: Mapped[str] = mapped_column(String(50))
    columns: Mapped[list] = mapped_column(JSONB)
    row_count: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('source_id', 'position', name='uq_spreadsheet_position'),)
