"""Retain delivery state and worker leases across restarts."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0014_push_delivery_state'
down_revision = '0013_inbox_push'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('inbox_push_subscriptions', sa.Column(
        'notification_state', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column('inbox_push_subscriptions', sa.Column(
        'next_attempt_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.alter_column('inbox_push_subscriptions', 'notification_state', server_default=None)
    op.alter_column('inbox_push_subscriptions', 'next_attempt_at', server_default=None)


def downgrade():
    op.drop_column('inbox_push_subscriptions', 'next_attempt_at')
    op.drop_column('inbox_push_subscriptions', 'notification_state')
