"""Store workspace follow-up rules and index recent send claims."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0060_lead_follow_ups'
down_revision = '0059_industry_templates'
branch_labels = depends_on = None


def upgrade():
    op.create_table('lead_follow_up_rules',
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('template_id', sa.String(36), sa.ForeignKey('templates.id', ondelete='SET NULL'), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('delay_hours', sa.Integer(), nullable=False),
        sa.Column('values', JSONB(), nullable=False),
        sa.Column('snapshot', JSONB(), nullable=False),
        sa.Column('configured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('next_check_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_messages_lead_follow_up_claims', 'messages', ['dispatch_started_at'],
                    postgresql_where=sa.text("reply_key = 'lead-follow-up'"))


def downgrade():
    op.drop_index('ix_messages_lead_follow_up_claims', table_name='messages')
    op.drop_table('lead_follow_up_rules')
