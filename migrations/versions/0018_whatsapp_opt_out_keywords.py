"""Add workspace-specific WhatsApp opt-out keywords."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0018_whatsapp_opt_out_keywords'
down_revision = '0017_channels'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('whatsapp_opt_out_keywords', JSONB, nullable=False,
                                      server_default=sa.text("'[]'::jsonb")))
    op.alter_column('tenants', 'whatsapp_opt_out_keywords', server_default=None)


def downgrade():
    op.drop_column('tenants', 'whatsapp_opt_out_keywords')
