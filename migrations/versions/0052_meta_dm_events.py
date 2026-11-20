"""Durable inbound Instagram and Messenger events."""
import sqlalchemy as sa
from alembic import op

revision = '0052_meta_dm_events'
down_revision = '0051_knowledge_sync'
branch_labels = depends_on = None


def upgrade():
    op.create_table('meta_dm_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel', sa.String(20), nullable=False),
        sa.Column('provider_message_id', sa.String(255), nullable=False, unique=True),
        sa.Column('recipient_id', sa.String(64), nullable=False),
        sa.Column('sender_id', sa.String(64), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_meta_dm_events_tenant_id', 'meta_dm_events', ['tenant_id'])


def downgrade():
    op.drop_index('ix_meta_dm_events_tenant_id', table_name='meta_dm_events')
    op.drop_table('meta_dm_events')
