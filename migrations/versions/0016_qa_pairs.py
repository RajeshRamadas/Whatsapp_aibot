"""Add workspace FAQ questions and embeddings."""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from app.config import settings

revision = '0016_qa_pairs'
down_revision = '0015_leads'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'qa_pairs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(settings.embedding_dimensions), nullable=False),
    )
    op.create_index('ix_qa_pairs_tenant_id', 'qa_pairs', ['tenant_id'])


def downgrade():
    op.drop_table('qa_pairs')
