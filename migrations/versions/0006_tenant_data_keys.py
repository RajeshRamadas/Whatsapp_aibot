"""Store wrapped per-tenant data keys with key versions."""
import sqlalchemy as sa
from alembic import op

revision = '0006_tenant_data_keys'
down_revision = '0005_audit_log'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('tenant_data_keys',
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('version', sa.Integer(), primary_key=True),
        sa.Column('master_key_version', sa.Integer(), nullable=False),
        sa.Column('nonce', sa.LargeBinary(), nullable=False),
        sa.Column('encrypted_key', sa.LargeBinary(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_tenant_data_keys_active', 'tenant_data_keys', ['tenant_id'], unique=True,
                    postgresql_where=sa.text('active'))


def downgrade():
    op.drop_table('tenant_data_keys')
