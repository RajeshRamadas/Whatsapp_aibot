"""Add a billing sponsor and durable attribution for pooled AI usage."""
import sqlalchemy as sa
from alembic import op

revision = '0062_agency_billing'
down_revision = '0061_support_sessions'
branch_labels = depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('agency_id', sa.String(36),
        sa.ForeignKey('tenants.id', name='fk_tenant_agency', ondelete='SET NULL')))
    op.create_index('ix_tenants_agency_id', 'tenants', ['agency_id'])
    op.add_column('usage_events', sa.Column('billing_tenant_id', sa.String(36),
        sa.ForeignKey('tenants.id', name='fk_usage_billing_tenant', ondelete='SET NULL')))
    op.create_index('ix_usage_billing_created', 'usage_events', ['billing_tenant_id', 'created_at'],
                    postgresql_where=sa.text("billing_tenant_id IS NOT NULL AND kind = 'answer'"))


def downgrade():
    op.drop_index('ix_usage_billing_created', table_name='usage_events')
    op.drop_column('usage_events', 'billing_tenant_id')
    op.drop_index('ix_tenants_agency_id', table_name='tenants')
    op.drop_column('tenants', 'agency_id')
