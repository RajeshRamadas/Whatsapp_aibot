"""Verified portfolio identity and rolling-capacity lookup indexes."""
import sqlalchemy as sa
from alembic import op

revision = '0029_campaign_portfolio'
down_revision = '0028_campaigns'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('channels', sa.Column('campaign_portfolio', sa.String(64)))
    op.create_index('ix_channels_campaign_portfolio', 'channels', ['campaign_portfolio'])
    op.create_index('ix_campaign_recipients_claimed_at', 'campaign_recipients', ['claimed_at'])


def downgrade():
    op.drop_index('ix_campaign_recipients_claimed_at', table_name='campaign_recipients')
    op.drop_index('ix_channels_campaign_portfolio', table_name='channels')
    op.drop_column('channels', 'campaign_portfolio')
