"""Store citations alongside assistant messages."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0012_message_sources'
down_revision = '0011_reply_dispatch'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('messages', sa.Column('sources', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.alter_column('messages', 'sources', server_default=None)


def downgrade():
    op.drop_column('messages', 'sources')
