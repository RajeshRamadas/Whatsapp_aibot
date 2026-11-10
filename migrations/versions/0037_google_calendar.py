"""Encrypted Google OAuth connections and durable operations."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0037_google_calendar'
down_revision = '0036_appointments'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('google_calendar_connections',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('label', sa.String(100), nullable=False), sa.Column('status', sa.String(24), nullable=False),
        sa.Column('revision', sa.Integer, nullable=False), sa.Column('credentials_enc', sa.LargeBinary),
        *[sa.Column(key, sa.DateTime(timezone=True)) for key in ('token_expires_at', 'token_lease_until',
                                                               'oauth_expires_at', 'last_success_at')],
        sa.Column('oauth_state_hash', sa.String(64), unique=True), sa.Column('oauth_session_hash', sa.String(64)),
        sa.Column('oauth_user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('oauth_secret_enc', sa.LargeBinary), sa.Column('calendars', JSONB, nullable=False),
        sa.Column('last_error', sa.String(64)), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_google_calendar_connections_tenant_id', 'google_calendar_connections', ['tenant_id'])
    op.create_table('google_calendar_tasks', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('connection_id', sa.String(36),
                  sa.ForeignKey('google_calendar_connections.id', ondelete='CASCADE')),
        sa.Column('kind', sa.String(24), nullable=False), sa.Column('resource_id', sa.String(36)),
        sa.Column('revision', sa.Integer, nullable=False), sa.Column('payload', JSONB, nullable=False),
        sa.Column('state', sa.String(16), nullable=False), sa.Column('attempts', sa.Integer, nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True)), sa.Column('last_error', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    for column in ('tenant_id', 'state', 'next_attempt_at'):
        op.create_index('ix_google_calendar_tasks_' + column, 'google_calendar_tasks', [column])


def downgrade():
    op.drop_table('google_calendar_tasks')
    op.drop_table('google_calendar_connections')
