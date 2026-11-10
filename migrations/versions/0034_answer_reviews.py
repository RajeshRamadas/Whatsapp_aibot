"""Answer review provenance and durable human-authored Q&A publication."""
import sqlalchemy as sa
from alembic import op

revision = '0034_answer_reviews'
down_revision = '0033_websites'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('answer_reviews',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('message_id', sa.BigInteger(), sa.ForeignKey('messages.id', ondelete='CASCADE'),
                  nullable=False, unique=True),
        sa.Column('widget_turn_id', sa.String(36), sa.ForeignKey('widget_turns.id', ondelete='SET NULL'), unique=True),
        sa.Column('question', sa.Text(), nullable=False), sa.Column('unanswered', sa.Boolean(), nullable=False),
        sa.Column('state', sa.String(16), nullable=False), sa.Column('draft_question', sa.Text()),
        sa.Column('draft_answer', sa.Text()),
        sa.Column('qa_pair_id', sa.String(36), sa.ForeignKey('qa_pairs.id', ondelete='SET NULL')),
        sa.Column('attempts', sa.Integer(), nullable=False), sa.Column('claimed_at', sa.DateTime(timezone=True)),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False), sa.Column('error', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    for name in ('tenant_id', 'created_at', 'next_attempt_at'):
        op.create_index('ix_answer_reviews_' + name, 'answer_reviews', [name])


def downgrade():
    op.drop_table('answer_reviews')
