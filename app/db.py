import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = 'tenants'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    admin_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    public_key: Mapped[str] = mapped_column(String(80), unique=True)
    allowed_origins: Mapped[str] = mapped_column(Text, default='[]')
    # None means "use the server default" (CHAT_PROVIDER / CHAT_MODEL).
    chat_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    chat_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The WhatsApp Business phone number ID that routes inbound messages to this tenant.
    whatsapp_phone_number_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)


class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Chunk(Base):
    __tablename__ = 'chunks'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey('sources.id', ondelete='CASCADE'), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)  # order within the source
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. 'Policies > Refunds', or a sheet name
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(Vector(settings.embedding_dimensions))
    # Keyword index for exact terms (order numbers, product codes) that embeddings miss.
    search_text: Mapped[str] = mapped_column(TSVECTOR, Computed(
        f"to_tsvector('{settings.text_search_config}', coalesce(heading, '') || ' ' || content)", persisted=True))
    __table_args__ = (
        Index('ix_chunks_embedding_hnsw', 'embedding', postgresql_using='hnsw',
              postgresql_ops={'embedding': 'vector_cosine_ops'}),
        Index('ix_chunks_search_text', 'search_text', postgresql_using='gin'),
    )


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    channel: Mapped[str] = mapped_column(String(20), default='web')  # web | whatsapp
    # The WhatsApp user ID (phone number) for WhatsApp conversations.
    external_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # True while a human agent owns the conversation; the bot stays silent.
    handoff: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'channel', 'external_user'),)


class Message(Base):
    __tablename__ = 'messages'
    # Sequential so history order is exact even when rows share a timestamp.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | agent
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvalCase(Base):
    """A test question with what a correct answer must contain, used to measure accuracy."""
    __tablename__ = 'eval_cases'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    question: Mapped[str] = mapped_column(Text)
    expected_keywords: Mapped[list] = mapped_column(JSONB, default=list)  # all must appear in the answer
    expected_source: Mapped[str | None] = mapped_column(String(255), nullable=True)  # filename to be cited
    expect_no_answer: Mapped[bool] = mapped_column(Boolean, default=False)  # documents don't cover it
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WhatsAppInbound(Base):
    """Every inbound WhatsApp message ID we have accepted, so Meta's retries are processed once."""
    __tablename__ = 'whatsapp_inbound'
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def init_db() -> None:
    """Reject an unversioned or outdated database; never modify schema on app startup."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    config = Config(str(Path(__file__).resolve().parent.parent / 'alembic.ini'))
    config.set_main_option('script_location', str(Path(__file__).resolve().parent.parent / 'migrations'))
    expected = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as con:
        actual = MigrationContext.configure(con).get_current_revision()
    if actual != expected:
        raise RuntimeError(f'Database revision {actual!r} does not match required {expected!r}; '
                           'run alembic upgrade head before starting the API')


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(200))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class UserSession(Base):
    __tablename__ = 'user_sessions'
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)


class AuthCode(Base):
    __tablename__ = 'auth_codes'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
