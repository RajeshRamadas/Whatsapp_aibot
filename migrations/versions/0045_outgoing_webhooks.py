"""Outgoing webhook endpoints and durable delivery history."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0045_outgoing_webhooks'
down_revision = '0044_public_api'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('webhook_endpoints',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False), sa.Column('url', sa.String(2048), nullable=False),
        sa.Column('secret_enc', sa.LargeBinary, nullable=False),
        sa.Column('secret_last4', sa.String(4), nullable=False),
        sa.Column('events', JSONB, nullable=False), sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('disabled_reason', sa.String(32)), sa.Column('failure_since', sa.DateTime(timezone=True)),
        sa.Column('last_success_at', sa.DateTime(timezone=True)), sa.Column('last_error', sa.String(40)),
        sa.Column('notification_pending', sa.Boolean, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'url', name='uq_webhook_endpoint_url'))
    op.create_table('webhook_deliveries',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('endpoint_id', sa.String(36),
                  sa.ForeignKey('webhook_endpoints.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_key', sa.String(160), nullable=False), sa.Column('payload', JSONB, nullable=False),
        sa.Column('state', sa.String(16), nullable=False), sa.Column('attempts', sa.Integer, nullable=False),
        sa.Column('history', JSONB, nullable=False), sa.Column('response_code', sa.Integer),
        sa.Column('last_error', sa.String(40)),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True)),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('endpoint_id', 'event_key', name='uq_webhook_delivery_event'))
    for table, columns in {'webhook_endpoints': ['tenant_id'], 'webhook_deliveries': [
            'tenant_id', 'endpoint_id', 'state', 'next_attempt_at', 'created_at']}.items():
        for column in columns:
            op.create_index(f'ix_{table}_{column}', table, [column])


def downgrade():
    op.drop_table('webhook_deliveries')
    op.drop_table('webhook_endpoints')
