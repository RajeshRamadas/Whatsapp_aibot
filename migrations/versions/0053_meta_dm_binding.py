"""Bind Meta history to its verified channel and preserve provider timestamps."""
import sqlalchemy as sa
from alembic import op

revision = '0053_meta_dm_binding'
down_revision = '0052_meta_dm_events'
branch_labels = depends_on = None


def upgrade():
    op.add_column('conversations', sa.Column('meta_channel_id', sa.String(36),
        sa.ForeignKey('channels.id', ondelete='SET NULL')))
    op.add_column('meta_dm_events', sa.Column('channel_id', sa.String(36),
        sa.ForeignKey('channels.id', ondelete='SET NULL')))
    op.add_column('meta_dm_events', sa.Column('sent_at', sa.DateTime(timezone=True)))
    op.drop_constraint('conversations_tenant_id_channel_external_user_key', 'conversations', type_='unique')
    op.create_index('uq_conversations_non_meta', 'conversations', ['tenant_id', 'channel', 'external_user'],
        unique=True, postgresql_where=sa.text("channel NOT IN ('instagram', 'messenger')"))
    op.create_index('uq_conversations_meta', 'conversations', ['tenant_id', 'meta_channel_id', 'external_user'],
        unique=True, postgresql_where=sa.text("channel IN ('instagram', 'messenger')"))
    # Never guess which asset owned old conversations/events. They remain read-only.


def downgrade():
    # Refuse conflicting legacy identities rather than delete customer history.
    op.create_unique_constraint('conversations_tenant_id_channel_external_user_key', 'conversations',
                                ['tenant_id', 'channel', 'external_user'])
    op.drop_index('uq_conversations_meta', table_name='conversations')
    op.drop_index('uq_conversations_non_meta', table_name='conversations')
    op.drop_column('meta_dm_events', 'sent_at')
    op.drop_column('meta_dm_events', 'channel_id')
    op.drop_column('conversations', 'meta_channel_id')
