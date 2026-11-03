"""Create channels and backfill existing WhatsApp numbers."""
import sqlalchemy as sa
from alembic import op

revision = '0017_channels'
down_revision = '0016_qa_pairs'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'channels',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('external_id', sa.String(64), nullable=False),
        sa.Column('display_name', sa.String(200)),
        sa.Column('credentials_enc', sa.LargeBinary()),
        sa.Column('last_webhook_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('kind', 'external_id', name='uq_channel_external'),
        sa.UniqueConstraint('tenant_id', 'kind', name='uq_channel_tenant_kind'),
    )
    op.execute("""
        INSERT INTO channels (id, tenant_id, kind, external_id)
        SELECT id, id, 'whatsapp', whatsapp_phone_number_id FROM tenants
        WHERE whatsapp_phone_number_id IS NOT NULL AND whatsapp_phone_number_id <> ''
    """)


def downgrade():
    op.drop_table('channels')
