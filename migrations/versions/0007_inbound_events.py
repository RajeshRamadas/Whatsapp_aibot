"""Replace legacy WhatsApp deduplication with durable inbound events."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0007_inbound_events'
down_revision = '0006_tenant_data_keys'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('inbound_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('channel_message_id', sa.String(128), nullable=False, unique=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('payload', JSONB, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_inbound_events_tenant_id', 'inbound_events', ['tenant_id'])
    op.execute("""INSERT INTO inbound_events
        (id, channel_message_id, tenant_id, payload, status, attempts, received_at)
        SELECT md5(message_id), message_id, tenant_id, '{}'::jsonb, 'processed', 0, received_at
        FROM whatsapp_inbound""")
    op.drop_table('whatsapp_inbound')


def downgrade():
    op.create_table('whatsapp_inbound',
        sa.Column('message_id', sa.String(128), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_whatsapp_inbound_tenant_id', 'whatsapp_inbound', ['tenant_id'])
    op.execute("""INSERT INTO whatsapp_inbound (message_id, tenant_id, received_at)
        SELECT channel_message_id, tenant_id, received_at FROM inbound_events""")
    op.drop_table('inbound_events')
