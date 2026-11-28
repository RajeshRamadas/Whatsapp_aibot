"""Agency branding and proof of control over its public hostname."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class AgencyBrand(Base):
    __tablename__ = 'agency_brands'
    __table_args__ = (Index('uq_agency_verified_domain', 'domain', unique=True,
                            postgresql_where=text('verified_at IS NOT NULL')),)

    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    primary_color: Mapped[str] = mapped_column(String(7))
    secondary_color: Mapped[str] = mapped_column(String(7))
    domain: Mapped[str | None] = mapped_column(String(253))
    challenge: Mapped[str | None] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
