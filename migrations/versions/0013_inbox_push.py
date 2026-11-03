"""Store workspace-scoped Inbox push subscriptions."""
import sqlalchemy as sa
from alembic import op

revision = '0013_inbox_push'
down_revision = '0012_message_sources'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'inbox_push_subscriptions',
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('endpoint_hash', sa.String(64), primary_key=True),
        sa.Column('endpoint', sa.Text(), nullable=False),
        sa.Column('p256dh', sa.String(128), nullable=False),
        sa.Column('auth', sa.String(64), nullable=False),
    )


def downgrade():
    op.drop_table('inbox_push_subscriptions')
