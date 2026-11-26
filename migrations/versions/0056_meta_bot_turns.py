"""Opt-in Meta knowledge replies and durable generation claims."""
import sqlalchemy as sa
from alembic import op

revision = '0056_meta_bot_turns'
down_revision = '0055_meta_receipts'
branch_labels = depends_on = None


def upgrade():
    op.add_column('channels', sa.Column('meta_bot_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('meta_bot_turns',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_id', sa.String(36), sa.ForeignKey('channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('incoming_message_id', sa.BigInteger(), sa.ForeignKey('messages.id', ondelete='CASCADE'),
                  nullable=False, unique=True),
        sa.Column('status', sa.String(16), nullable=False), sa.Column('claim_token', sa.String(36)),
        sa.Column('claimed_at', sa.DateTime(timezone=True)), sa.Column('error', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_meta_bot_turns_tenant_id', 'meta_bot_turns', ['tenant_id'])
    op.create_index('ix_meta_bot_turns_work', 'meta_bot_turns', ['status', 'incoming_message_id'])


def downgrade():
    op.drop_table('meta_bot_turns')
    op.drop_column('channels', 'meta_bot_enabled')
