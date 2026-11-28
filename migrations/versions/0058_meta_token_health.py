"""Schedule credential checks for connected Meta messaging accounts."""
import sqlalchemy as sa
from alembic import op

revision = '0058_meta_token_health'
down_revision = '0057_meta_inbound_media'
branch_labels = depends_on = None


def upgrade():
    op.add_column('channels', sa.Column('meta_token_status', sa.String(16)))
    op.add_column('channels', sa.Column('meta_token_check_at', sa.DateTime(timezone=True)))
    op.add_column('channels', sa.Column('meta_token_expires_at', sa.DateTime(timezone=True)))
    op.add_column('channels', sa.Column('meta_token_alerted', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_index('ix_channels_meta_token_check_at', 'channels', ['meta_token_check_at'])


def downgrade():
    op.drop_index('ix_channels_meta_token_check_at', table_name='channels')
    op.drop_column('channels', 'meta_token_alerted')
    op.drop_column('channels', 'meta_token_expires_at')
    op.drop_column('channels', 'meta_token_check_at')
    op.drop_column('channels', 'meta_token_status')
