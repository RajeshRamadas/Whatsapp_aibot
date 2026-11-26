"""Asset-bound delivery/read receipts and conservative watermark correlation."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0055_meta_receipts'
down_revision = '0054_meta_authorizations'
branch_labels = depends_on = None


def upgrade():
    op.add_column('conversations', sa.Column('meta_read_watermark', sa.DateTime(timezone=True)))
    op.add_column('messages', sa.Column('meta_acknowledged_at', sa.DateTime(timezone=True)))
    op.create_table('meta_receipts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_id', sa.String(36), sa.ForeignKey('channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sender_id', sa.String(64), nullable=False), sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('mids', JSONB(), nullable=False), sa.Column('watermark', sa.DateTime(timezone=True)),
        sa.Column('dedupe_key', sa.String(64), nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('channel_id', 'dedupe_key', name='uq_meta_receipt'))
    op.create_index('ix_meta_receipts_tenant_id', 'meta_receipts', ['tenant_id'])
    op.create_index('ix_meta_receipts_received_at', 'meta_receipts', ['received_at'])
    op.create_index('ix_meta_receipts_ready', 'meta_receipts', ['status', 'next_attempt_at'])


def downgrade():
    op.drop_table('meta_receipts')
    op.drop_column('messages', 'meta_acknowledged_at')
    op.drop_column('conversations', 'meta_read_watermark')
