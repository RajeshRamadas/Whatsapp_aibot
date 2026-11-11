import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
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
    lead_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    admin_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    public_key: Mapped[str] = mapped_column(String(80), unique=True)
    allowed_origins: Mapped[str] = mapped_column(Text, default='[]')
    # None means "use the server default" (CHAT_PROVIDER / CHAT_MODEL).
    chat_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    chat_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The WhatsApp Business phone number ID that routes inbound messages to this tenant.
    whatsapp_phone_number_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    bot_name: Mapped[str] = mapped_column(String(100), default='Assistant')
    bot_persona: Mapped[str] = mapped_column(Text, default='')
    bot_tone: Mapped[str] = mapped_column(String(20), default='neutral')
    bot_rules: Mapped[str] = mapped_column(Text, default='')
    business_hours: Mapped[str] = mapped_column(Text, default='')
    timezone: Mapped[str] = mapped_column(String(64), default='UTC')
    plan_id: Mapped[str] = mapped_column(ForeignKey('plans.id'), default='starter', server_default='starter')
    billing_details: Mapped[dict | None] = mapped_column(JSONB)
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: now() + timedelta(days=14),
                                                    server_default=text("now() + interval '14 days'"))
    quota_behavior: Mapped[str] = mapped_column(String(16), default='pause', server_default='pause')
    widget_theme: Mapped[dict] = mapped_column(JSONB, default=dict)
    whatsapp_opt_out_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    bot_messages: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    default_language: Mapped[str] = mapped_column(String(16), default='en')
    bot_live: Mapped[bool] = mapped_column(Boolean, default=False)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))


class Website(Base):
    __tablename__ = 'websites'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    url: Mapped[str] = mapped_column(Text)
    max_pages: Mapped[int] = mapped_column(Integer, default=1)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    schedule: Mapped[str] = mapped_column(String(12), default='manual')
    status: Mapped[str] = mapped_column(String(16), default='queued')
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    generation: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=now, index=True)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'url', name='uq_website_url'),)


class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str] = mapped_column(Text)
    website_id: Mapped[str | None] = mapped_column(ForeignKey('websites.id', ondelete='CASCADE'), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(64))
    crawl_depth: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('website_id', 'url', name='uq_website_page'),)


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


class Contact(Base):
    """Tenant-scoped identity for a customer on one channel."""
    __tablename__ = 'contacts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    external_user: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opt_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_source: Mapped[str | None] = mapped_column(String(120))
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint('tenant_id', 'channel', 'external_user'),)


class ContactSegment(Base):
    __tablename__ = 'contact_segments'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'name'),)


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    channel: Mapped[str] = mapped_column(String(20), default='web')  # web | whatsapp
    # The WhatsApp user ID (phone number) for WhatsApp conversations.
    external_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # True while a human agent owns the conversation; the bot stays silent.
    handoff: Mapped[bool] = mapped_column(Boolean, default=False)
    analytics_handoff: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    contact_id: Mapped[str | None] = mapped_column(ForeignKey('contacts.id', ondelete='SET NULL'), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='bot')
    assignee_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    labels: Mapped[list] = mapped_column(JSONB, default=list)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'channel', 'external_user'),)


class Message(Base):
    __tablename__ = 'messages'
    __table_args__ = (UniqueConstraint('conversation_id', 'reply_key', name='uq_message_reply_key'),)
    # Sequential so history order is exact even when rows share a timestamp.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | agent
    direction: Mapped[str] = mapped_column(String(12), default='inbound')
    author_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default='text')
    channel_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    reply_key: Mapped[str | None] = mapped_column(String(128))
    template_payload: Mapped[dict | None] = mapped_column(JSONB)
    attachments: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    upload_data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    dispatch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)




class CannedReply(Base):
    __tablename__ = 'canned_replies'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
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


class InboundEvent(Base):
    """Durable, deduplicated inbound WhatsApp events."""
    __tablename__ = 'inbound_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_message_id: Mapped[str] = mapped_column(String(128), unique=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False)
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


class Membership(Base):
    __tablename__ = 'memberships'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(16))
    __table_args__ = (UniqueConstraint('user_id', 'tenant_id'),)


class Invitation(Base):
    __tablename__ = 'invitations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))


class ApiKey(Base):
    __tablename__ = 'api_keys'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(24))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = 'audit_log'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str | None] = mapped_column(String(120), nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class TenantDataKey(Base):
    __tablename__ = 'tenant_data_keys'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_key_version: Mapped[int] = mapped_column(Integer)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (Index('ix_tenant_data_keys_active', 'tenant_id', unique=True,
                            postgresql_where=text('active')),)



class InboxPushSubscription(Base):
    __tablename__ = 'inbox_push_subscriptions'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    endpoint_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    notification_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    endpoint: Mapped[str] = mapped_column(Text)
    p256dh: Mapped[str] = mapped_column(String(128))
    auth: Mapped[str] = mapped_column(String(64))


class Lead(Base):
    __tablename__ = 'leads'
    sync_version: Mapped[int] = mapped_column(Integer, default=0)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='new')
    owner_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    notes: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class QAPair(Base):
    __tablename__ = 'qa_pairs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(Vector(settings.embedding_dimensions))


class Channel(Base):
    __tablename__ = 'channels'
    __table_args__ = (
        UniqueConstraint('kind', 'external_id', name='uq_channel_external'),
        UniqueConstraint('tenant_id', 'kind', name='uq_channel_tenant_kind'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(200))
    waba_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    last_webhook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    campaign_quality: Mapped[str] = mapped_column(String(16), default='UNKNOWN', server_default='UNKNOWN')
    campaign_limit: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    campaign_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    campaign_portfolio: Mapped[str | None] = mapped_column(String(64), index=True)


class Template(Base):
    __tablename__ = 'templates'
    __table_args__ = (UniqueConstraint('channel_id', 'meta_id', name='uq_template_channel_meta'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str] = mapped_column(ForeignKey('channels.id', ondelete='CASCADE'))
    meta_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(512))
    language: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    body: Mapped[str | None] = mapped_column(Text)


class Campaign(Base):
    __tablename__ = 'campaigns'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    channel_id: Mapped[str | None] = mapped_column(ForeignKey('channels.id', ondelete='SET NULL'))
    template_id: Mapped[str | None] = mapped_column(ForeignKey('templates.id', ondelete='SET NULL'))
    name: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(16), default='draft')
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    bindings: Mapped[list] = mapped_column(JSONB)
    excluded: Mapped[int] = mapped_column(Integer, default=0)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    currency: Mapped[str] = mapped_column(String(3), default='INR')
    reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CampaignRecipient(Base):
    __tablename__ = 'campaign_recipients'
    __table_args__ = (UniqueConstraint('campaign_id', 'contact_id', 'is_test'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey('campaigns.id', ondelete='CASCADE'), index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey('contacts.id', ondelete='CASCADE'))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    values: Mapped[list] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(16), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    provider_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    billable: Mapped[bool | None] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(32))


class WidgetTurn(Base):
    __tablename__ = 'widget_turns'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    message: Mapped[str] = mapped_column(Text)
    handoff: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(16), default='queued')
    answer: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    feedback: Mapped[str | None] = mapped_column(String(4))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AnswerReview(Base):
    __tablename__ = 'answer_reviews'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), unique=True)
    widget_turn_id: Mapped[str | None] = mapped_column(ForeignKey('widget_turns.id', ondelete='SET NULL'), unique=True)
    question: Mapped[str] = mapped_column(Text)
    unanswered: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(16), default='open')
    draft_question: Mapped[str | None] = mapped_column(Text)
    draft_answer: Mapped[str | None] = mapped_column(Text)
    qa_pair_id: Mapped[str | None] = mapped_column(ForeignKey('qa_pairs.id', ondelete='SET NULL'))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Plan(Base):
    __tablename__ = 'plans'
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    limits: Mapped[dict] = mapped_column(JSONB)


class UsageEvent(Base):
    __tablename__ = 'usage_events'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'request_key', name='uq_usage_request'),
        Index('ix_usage_tenant_kind_created', 'tenant_id', 'kind', 'created_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String(20))
    request_key: Mapped[str | None] = mapped_column(String(150))
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    audio_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    ocr_pages: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    overage: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UsageDaily(Base):
    __tablename__ = 'usage_daily'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    ai_messages: Mapped[int] = mapped_column(BigInteger)
    overage_messages: Mapped[int] = mapped_column(BigInteger)
    ai_calls: Mapped[int] = mapped_column(BigInteger)
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    unpriced_calls: Mapped[int] = mapped_column(BigInteger)


class Subscription(Base):
    __tablename__ = 'subscriptions'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True)
    provider_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    checkout_key: Mapped[str] = mapped_column(String(36))
    checkout_url: Mapped[str | None] = mapped_column(Text)
    plan_id: Mapped[str] = mapped_column(ForeignKey('plans.id'))
    status: Mapped[str] = mapped_column(String(24))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_issue: Mapped[str | None] = mapped_column(String(200))
    settlement_issue: Mapped[str | None] = mapped_column(String(200))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BillingEvent(Base):
    __tablename__ = 'billing_events'
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event: Mapped[str] = mapped_column(String(40))
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'))
    payment_id: Mapped[str | None] = mapped_column(String(64))
    amount_paise: Mapped[int | None] = mapped_column(BigInteger)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WebhookFailure(Base):
    """Operational metadata only; never store request bodies or credentials here."""
    __tablename__ = 'webhook_failures'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String(20))
    http_status: Mapped[int] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class EmailOutbox(Base):
    __tablename__ = 'email_outbox'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    recipient_hash: Mapped[str] = mapped_column(String(64))
    payload_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(16), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    provider_id: Mapped[str | None] = mapped_column(String(128))


class EmailSuppression(Base):
    __tablename__ = 'email_suppressions'
    recipient_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailFeedbackEvent(Base):
    __tablename__ = 'email_feedback_events'
    id: Mapped[str] = mapped_column(String(150), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProductEvent(Base):
    """Allowlisted event names and opaque deduplication IDs; no arbitrary properties."""
    __tablename__ = 'product_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'))
    name: Mapped[str] = mapped_column(String(64))
    event_key: Mapped[str] = mapped_column(String(150), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (Index('ix_product_events_tenant_time', 'tenant_id', 'occurred_at'),)


class AnalyticsDaily(Base):
    __tablename__ = 'analytics_daily'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), primary_key=True)
    conversations: Mapped[int] = mapped_column(BigInteger)
    messages: Mapped[int] = mapped_column(BigInteger)
    bot_answered: Mapped[int] = mapped_column(BigInteger)
    handed_off: Mapped[int] = mapped_column(BigInteger)
    leads: Mapped[int] = mapped_column(BigInteger)
    response_ms: Mapped[int] = mapped_column(BigInteger)
    response_count: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@event.listens_for(Session, 'before_flush')
def remember_handoff(db, flush_context, instances):
    for row in db.new.union(db.dirty):
        if isinstance(row, Conversation) and row.status != 'closed' and (
                row.handoff or row.status in ('waiting', 'agent')):
            row.analytics_handoff = True


class InvoiceSequence(Base):
    __tablename__ = 'invoice_sequences'
    financial_year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_number: Mapped[int] = mapped_column(Integer)


class Invoice(Base):
    __tablename__ = 'invoices'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey('tenants.id', ondelete='SET NULL'), index=True)
    payment_id: Mapped[str] = mapped_column(String(64), unique=True)
    number: Mapped[str] = mapped_column(String(16), unique=True)
    issued_on: Mapped[date] = mapped_column(Date)
    snapshot: Mapped[dict] = mapped_column(JSONB)

# Register domain tables without expanding this shared module further.
from .models import appointments as _appointments  # noqa: E402, F401
from .models import evaluations as _evaluations  # noqa: E402,F401
from .models import google_calendar as _google_calendar  # noqa: E402, F401
from .models import lead_sync as _lead_sync  # noqa: E402,F401
from .models import mfa as _mfa  # noqa: E402,F401
from .models import payments as _payments  # noqa: E402,F401
from .models import public_api as _public_api  # noqa: E402,F401
from .models import webhooks as _webhooks  # noqa: E402,F401
