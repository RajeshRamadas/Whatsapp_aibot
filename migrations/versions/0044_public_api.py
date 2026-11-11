"""Public API idempotency ledger and background knowledge operations."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0044_public_api'
down_revision = '0043_user_mfa'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('api_requests', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key_hash', sa.String(64), nullable=False), sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('status', sa.Integer, nullable=False), sa.Column('response', JSONB, nullable=False),
        sa.Column('headers', JSONB, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'key_hash', name='uq_api_request_key'))
    op.create_table('api_operations', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key_id', sa.String(36), sa.ForeignKey('api_keys.id', ondelete='SET NULL')),
        sa.Column('kind', sa.String(24), nullable=False), sa.Column('resource_id', sa.String(36), nullable=False),
        sa.Column('payload', JSONB, nullable=False), sa.Column('state', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer, nullable=False), sa.Column('claimed_at', sa.DateTime(timezone=True)),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_error', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    for table, columns in {'api_requests': ['tenant_id', 'expires_at'],
                           'api_operations': ['tenant_id', 'state', 'next_attempt_at']}.items():
        for column in columns:
            op.create_index(f'ix_{table}_{column}', table, [column])


def downgrade():
    op.drop_table('api_operations')
    op.drop_table('api_requests')
