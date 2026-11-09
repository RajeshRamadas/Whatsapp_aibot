"""Add privacy-limited product events and daily conversation analytics."""
import sqlalchemy as sa
from alembic import op

revision = '0025_analytics'
down_revision = '0024_platform_admin'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversations', sa.Column('analytics_handoff', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("""UPDATE conversations c SET analytics_handoff = true WHERE c.handoff
        OR c.status IN ('waiting', 'agent') OR EXISTS (
            SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.role = 'agent' AND m.kind != 'note')""")
    op.create_table('product_events',
                    sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE')),
                    sa.Column('name', sa.String(64), nullable=False),
                    sa.Column('event_key', sa.String(150), nullable=False, unique=True),
                    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_product_events_tenant_time', 'product_events', ['tenant_id', 'occurred_at'])
    op.create_table('analytics_daily',
                    sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
                    sa.Column('day', sa.Date(), primary_key=True),
                    sa.Column('channel', sa.String(20), primary_key=True),
                    *(sa.Column(name, sa.BigInteger(), nullable=False) for name in (
                        'conversations', 'messages', 'bot_answered', 'handed_off', 'leads', 'response_ms', 'response_count')),
                    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('analytics_daily')
    op.drop_table('product_events')
    op.drop_column('conversations', 'analytics_handoff')
