"""Tenant-owned localized bot notice overrides."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0035_bot_messages'
down_revision = '0034_answer_reviews'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('bot_messages', JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade():
    op.drop_column('tenants', 'bot_messages')
