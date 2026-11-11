"""Explicit lead-sync connections and durable lead-version deliveries."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0041_lead_sync'
down_revision = '0040_payment_links'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('leads', sa.Column('sync_version', sa.Integer, nullable=False, server_default='0'))
    op.alter_column('leads', 'sync_version', server_default=None)
    op.create_table('lead_connections', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        *[sa.Column(key, sa.String(size), nullable=False) for key, size in
          [('provider', 16), ('label', 100), ('region', 8), ('status', 24)]],
        sa.Column('enabled', sa.Boolean, nullable=False), sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('credentials_enc', sa.LargeBinary), sa.Column('key_last4', sa.String(4)),
        sa.Column('config', JSONB, nullable=False), sa.Column('catalog', JSONB, nullable=False),
        sa.Column('oauth_state_hash', sa.String(64), unique=True), sa.Column('oauth_session_hash', sa.String(64)),
        sa.Column('oauth_user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('oauth_secret_enc', sa.LargeBinary),
        *[sa.Column(key, sa.DateTime(timezone=True)) for key in ('lease_until', 'oauth_expires_at', 'last_success_at')],
        sa.Column('last_error', sa.String(64)), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('lead_deliveries', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('connection_id', sa.String(36),
                  sa.ForeignKey('lead_connections.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='SET NULL')),
        sa.Column('kind', sa.String(16), nullable=False), sa.Column('lead_version', sa.Integer, nullable=False),
        sa.Column('revision', sa.Integer, nullable=False), sa.Column('payload', JSONB, nullable=False),
        sa.Column('state', sa.String(16), nullable=False), sa.Column('attempts', sa.Integer, nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True)), sa.Column('remote_id', sa.String(100)),
        *[sa.Column(key, sa.Boolean, nullable=False) for key in ('submitted', 'note_submitted', 'contact_done')],
        sa.Column('last_error', sa.String(64)), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('connection_id', 'conversation_id', 'lead_version', 'revision', name='uq_lead_delivery_version'))
    for table, columns in {'lead_connections': ['tenant_id'],
            'lead_deliveries': ['tenant_id', 'connection_id', 'conversation_id', 'state', 'next_attempt_at']}.items():
        for column in columns:
            op.create_index('ix_' + table + '_' + column, table, [column])


def downgrade():
    op.drop_table('lead_deliveries')
    op.drop_table('lead_connections')
    op.drop_column('leads', 'sync_version')
