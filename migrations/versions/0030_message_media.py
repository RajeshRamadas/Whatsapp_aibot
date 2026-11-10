"""Private message attachments and temporary outbound upload staging."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0030_message_media'
down_revision = '0029_campaign_portfolio'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('messages', sa.Column('attachments', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column('messages', sa.Column('upload_data', sa.LargeBinary()))


def downgrade():
    op.drop_column('messages', 'upload_data')
    op.drop_column('messages', 'attachments')
