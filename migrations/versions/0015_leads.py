"""Add lead capture and tenant field configuration."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0015_leads'
down_revision = '0014_push_delivery_state'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('lead_fields', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.alter_column('tenants', 'lead_fields', server_default=None)
    op.create_table(
        'leads',
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('fields', JSONB, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('notes', sa.Text, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_leads_tenant_id', 'leads', ['tenant_id'])


def downgrade():
    op.drop_table('leads')
    op.drop_column('tenants', 'lead_fields')
