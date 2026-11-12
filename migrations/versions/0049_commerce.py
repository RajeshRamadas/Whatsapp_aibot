"""Read-only store connections and durable catalogue sync leases."""
import sqlalchemy as sa
from alembic import op

revision = '0049_commerce'
down_revision = '0048_spreadsheet_tables'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('commerce_stores',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False,
                  unique=True),
        sa.Column('provider', sa.String(16), nullable=False), sa.Column('url', sa.String(255), nullable=False),
        sa.Column('credentials_enc', sa.LargeBinary, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('source_id', sa.String(36), sa.ForeignKey('sources.id', ondelete='SET NULL')),
        sa.Column('next_run', sa.DateTime(timezone=True)), sa.Column('lease', sa.String(36)),
        sa.Column('attempts', sa.Integer, nullable=False), sa.Column('product_count', sa.Integer, nullable=False),
        sa.Column('last_success', sa.DateTime(timezone=True)), sa.Column('last_error', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_commerce_stores_next_run', 'commerce_stores', ['next_run'])


def downgrade():
    # Remove generated knowledge as well as credentials when rolling back.
    op.execute("DELETE FROM sources WHERE id IN (SELECT source_id FROM commerce_stores)")
    op.drop_table('commerce_stores')
