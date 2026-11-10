"""Meter OCR pages independently of tokens and audio duration."""
import sqlalchemy as sa
from alembic import op

revision = '0032_ocr_usage'
down_revision = '0031_audio_usage'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('usage_events', sa.Column('ocr_pages', sa.Integer()))


def downgrade():
    op.drop_column('usage_events', 'ocr_pages')
