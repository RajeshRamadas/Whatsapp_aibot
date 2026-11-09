"""Add tenant suspension and payload-free webhook failure history."""
import sqlalchemy as sa
from alembic import op

revision = '0024_platform_admin'
down_revision = '0023_gst_invoices'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('suspended', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('webhook_failures',
                    sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('provider', sa.String(20), nullable=False),
                    sa.Column('http_status', sa.Integer(), nullable=False),
                    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_webhook_failures_received_at', 'webhook_failures', ['received_at'])


def downgrade():
    op.drop_table('webhook_failures')
    op.drop_column('tenants', 'suspended')
