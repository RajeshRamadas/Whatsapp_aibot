"""Track when an outbound reply was claimed."""
import sqlalchemy as sa
from alembic import op

revision = '0011_reply_dispatch'
down_revision = '0010_canned_replies'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('messages', sa.Column('dispatch_started_at', sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column('messages', 'dispatch_started_at')
