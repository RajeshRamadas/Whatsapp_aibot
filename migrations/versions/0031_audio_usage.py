"""Meter actual audio duration without presenting it as token usage."""
import sqlalchemy as sa
from alembic import op

revision = '0031_audio_usage'
down_revision = '0030_message_media'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('usage_events', sa.Column('audio_seconds', sa.Numeric(12, 3)))


def downgrade():
    op.drop_column('usage_events', 'audio_seconds')
