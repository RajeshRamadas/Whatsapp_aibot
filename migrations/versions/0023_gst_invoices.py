"""Add billing profiles and immutable invoice snapshots."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0023_gst_invoices'
down_revision = '0022_subscriptions'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('billing_details', JSONB))
    op.create_table(
        'invoice_sequences',
        sa.Column('financial_year', sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column('last_number', sa.Integer(), nullable=False),
    )
    op.create_table(
        'invoices',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='SET NULL')),
        sa.Column('payment_id', sa.String(64), nullable=False, unique=True),
        sa.Column('number', sa.String(16), nullable=False, unique=True),
        sa.Column('issued_on', sa.Date(), nullable=False),
        sa.Column('snapshot', JSONB, nullable=False),
    )
    op.create_index('ix_invoices_tenant_id', 'invoices', ['tenant_id'])


def downgrade():
    op.drop_table('invoices')
    op.drop_table('invoice_sequences')
    op.drop_column('tenants', 'billing_details')
