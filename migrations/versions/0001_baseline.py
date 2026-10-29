"""Initial v0.2 schema. Revision ID: 0001_baseline."""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from app.config import settings

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("admin_key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("public_key", sa.String(80), nullable=False, unique=True),
        sa.Column("allowed_origins", sa.Text(), nullable=False),
        sa.Column("chat_provider", sa.String(20)),
        sa.Column("chat_model", sa.String(100)),
        sa.Column("whatsapp_phone_number_id", sa.String(64), unique=True))
    op.create_table("sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_sources_tenant_id", "sources", ["tenant_id"])
    op.create_table("chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("heading", sa.Text()),
        sa.Column("page", sa.Integer()),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(settings.embedding_dimensions), nullable=False),
        sa.Column("search_text", TSVECTOR(), sa.Computed(
            "to_tsvector('%s', coalesce(heading, '') || ' ' || content)" % settings.text_search_config,
            persisted=True)))
    op.create_index("ix_chunks_tenant_id", "chunks", ["tenant_id"])
    op.create_index("ix_chunks_source_id", "chunks", ["source_id"])
    op.create_index("ix_chunks_embedding_hnsw", "chunks", ["embedding"],
                    postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"})
    op.create_index("ix_chunks_search_text", "chunks", ["search_text"], postgresql_using="gin")
    op.create_table("conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("external_user", sa.String(64)),
        sa.Column("handoff", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "channel", "external_user"))
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])
    op.create_table("messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.String(36),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_table("eval_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_keywords", JSONB(), nullable=False),
        sa.Column("expected_source", sa.String(255)),
        sa.Column("expect_no_answer", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_eval_cases_tenant_id", "eval_cases", ["tenant_id"])
    op.create_table("whatsapp_inbound",
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_whatsapp_inbound_tenant_id", "whatsapp_inbound", ["tenant_id"])


def downgrade():
    for table in ("whatsapp_inbound", "eval_cases", "messages", "conversations", "chunks", "sources", "tenants"):
        op.drop_table(table)
