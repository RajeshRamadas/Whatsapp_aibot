"""Tenant-scoped security audit events."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0005_audit_log'
down_revision = '0004_api_keys'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('audit_log',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_user_id', sa.String(36), nullable=True),
        sa.Column('actor_type', sa.String(20), nullable=False),
        sa.Column('action', sa.String(80), nullable=False),
        sa.Column('target', sa.String(120), nullable=True),
        sa.Column('details', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_audit_log_tenant_id', 'audit_log', ['tenant_id'])
    op.create_index('ix_audit_log_created_at', 'audit_log', ['created_at'])


def downgrade():
    op.drop_table('audit_log')
