"""Add WABA bindings, synced templates and queued template sends."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0019_templates'
down_revision = '0018_whatsapp_opt_out_keywords'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('channels', sa.Column('waba_id', sa.String(32)))
    op.create_unique_constraint('channels_waba_id_key', 'channels', ['waba_id'])
    op.add_column('messages', sa.Column('template_payload', JSONB))
    op.create_table(
        'templates',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('channel_id', sa.String(36), sa.ForeignKey('channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('meta_id', sa.String(64), nullable=False),
        sa.Column('name', sa.String(512), nullable=False),
        sa.Column('language', sa.String(32), nullable=False),
        sa.Column('category', sa.String(32), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('body', sa.Text()),
        sa.UniqueConstraint('channel_id', 'meta_id', name='uq_template_channel_meta'),
    )


def downgrade():
    op.drop_table('templates')
    op.drop_column('messages', 'template_payload')
    op.drop_constraint('channels_waba_id_key', 'channels', type_='unique')
    op.drop_column('channels', 'waba_id')
