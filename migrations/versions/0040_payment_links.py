"""Tenant Razorpay credentials, approved payment requests and webhook inbox."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0040_payment_links'
down_revision = '0039_calendar_bookings'
branch_labels = None
depends_on = None


def fk(name, target, delete='CASCADE', nullable=False):
    return sa.Column(name, sa.String(36), sa.ForeignKey(target, ondelete=delete), nullable=nullable)


def upgrade():
    op.create_table('payment_connections', sa.Column('id', sa.String(36), primary_key=True),
        fk('tenant_id', 'tenants.id'), sa.Column('label', sa.String(100), nullable=False),
        sa.Column('mode', sa.String(8), nullable=False), sa.Column('key_last4', sa.String(4), nullable=False),
        sa.Column('credentials_enc', sa.LargeBinary), sa.Column('webhook_secret_enc', sa.LargeBinary, nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        fk('receipt_template_id', 'templates.id', 'SET NULL', True),
        sa.Column('last_success_at', sa.DateTime(timezone=True)), sa.Column('last_error', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('payment_requests', sa.Column('id', sa.String(36), primary_key=True),
        fk('tenant_id', 'tenants.id'), fk('connection_id', 'payment_connections.id'),
        fk('conversation_id', 'conversations.id', 'SET NULL', True),
        fk('booking_id', 'appointment_bookings.id', 'SET NULL', True),
        sa.Column('approval_key', sa.String(80), nullable=False), sa.Column('amount', sa.Integer, nullable=False),
        sa.Column('description', sa.String(255), nullable=False), sa.Column('customer_name', sa.String(100), nullable=False),
        sa.Column('customer_phone', sa.String(16), nullable=False), sa.Column('language', sa.String(16), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(24), nullable=False), sa.Column('provider_id', sa.String(80)),
        sa.Column('url', sa.String(1024)), sa.Column('payment_id', sa.String(80)),
        sa.Column('cancel_requested', sa.Boolean, nullable=False),
        sa.Column('submitted', sa.Boolean, nullable=False), sa.Column('operation', sa.String(16)),
        sa.Column('job_state', sa.String(16), nullable=False), sa.Column('attempts', sa.Integer, nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True)), sa.Column('last_error', sa.String(64)),
        *[sa.Column(key, sa.BigInteger, sa.ForeignKey('messages.id', ondelete='SET NULL'))
          for key in ('link_message_id', 'receipt_message_id')],
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'approval_key', name='uq_payment_approval'))
    op.create_table('payment_events', sa.Column('id', sa.String(36), primary_key=True),
        fk('tenant_id', 'tenants.id'), fk('connection_id', 'payment_connections.id'),
        sa.Column('event_key', sa.String(64), nullable=False), sa.Column('payload_enc', sa.LargeBinary),
        sa.Column('metadata_json', JSONB, nullable=False), sa.Column('state', sa.String(16), nullable=False),
        sa.Column('last_error', sa.String(64)), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('connection_id', 'event_key', name='uq_payment_event'))
    for table, columns in {'payment_connections': ['tenant_id'],
            'payment_requests': ['tenant_id', 'conversation_id', 'job_state', 'next_attempt_at'],
            'payment_events': ['tenant_id', 'state']}.items():
        for column in columns:
            op.create_index('ix_' + table + '_' + column, table, [column])


def downgrade():
    for table in ('payment_events', 'payment_requests', 'payment_connections'):
        op.drop_table(table)
