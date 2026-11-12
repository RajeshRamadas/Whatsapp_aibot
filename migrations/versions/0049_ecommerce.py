"""Read-only store connections and leased catalogue sync."""
import sqlalchemy as sa
from alembic import op

revision = '0049_ecommerce'
down_revision = '0048_spreadsheet_tables'
branch_labels = depends_on = None


def upgrade():
    op.create_table('store_connections',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String(16), nullable=False), sa.Column('label', sa.String(100), nullable=False),
        sa.Column('origin', sa.String(255), nullable=False), sa.Column('credentials_enc', sa.LargeBinary, nullable=False),
        sa.Column('enabled', sa.Boolean, nullable=False), sa.Column('status', sa.String(24), nullable=False),
        sa.Column('source_id', sa.String(36), sa.ForeignKey('sources.id', ondelete='SET NULL')),
        sa.Column('product_count', sa.Integer, nullable=False), sa.Column('next_sync', sa.DateTime(timezone=True)),
        sa.Column('lease', sa.String(36)), sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('last_success_at', sa.DateTime(timezone=True)), sa.Column('last_error', sa.String(40)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    for name in ('tenant_id', 'source_id', 'next_sync'):
        op.create_index('ix_store_connections_' + name, 'store_connections', [name])


def downgrade():
    op.execute("DELETE FROM sources WHERE id IN (SELECT source_id FROM store_connections)")
    op.drop_table('store_connections')
