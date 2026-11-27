"""Private Meta attachment snapshots and download claims."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0057_meta_inbound_media'
down_revision = '0056_meta_bot_turns'
branch_labels = depends_on = None


def upgrade():
    op.add_column('meta_dm_events', sa.Column('attachments', JSONB(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column('meta_dm_events', sa.Column('media_claimed_at', sa.DateTime(timezone=True)))
    op.create_index('ix_meta_dm_events_work', 'meta_dm_events', ['status', 'received_at', 'id'])
    op.create_index('ix_meta_dm_events_media_expiry', 'meta_dm_events', ['received_at'],
                    postgresql_where=sa.text("attachments @? '$[*].source_enc'"))


def downgrade():
    op.drop_index('ix_meta_dm_events_media_expiry', table_name='meta_dm_events')
    op.drop_index('ix_meta_dm_events_work', table_name='meta_dm_events')
    op.drop_column('meta_dm_events', 'media_claimed_at')
    op.drop_column('meta_dm_events', 'attachments')
