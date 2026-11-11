"""Account-wide authenticator state and short-lived password-verified login challenges."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class UserMFA(Base):
    __tablename__ = 'user_mfa'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=0)
    last_step: Mapped[int] = mapped_column(BigInteger, default=-1)
    recovery_hashes: Mapped[list] = mapped_column(JSONB, default=list)
    pending_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    pending_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_session: Mapped[str | None] = mapped_column(String(64))
    pending_password: Mapped[str | None] = mapped_column(String(64))
    pending_attempts: Mapped[int] = mapped_column(Integer, default=0)


class MFAChallenge(Base):
    __tablename__ = 'mfa_challenges'
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    password_digest: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
