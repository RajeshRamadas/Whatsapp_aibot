"""Tenant bot settings and explicit go-live state."""
import sqlalchemy as sa
from alembic import op

revision = '0007_bot_settings'
down_revision = '0006_tenant_data_keys'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('bot_name', sa.String(100), nullable=False, server_default='Assistant'))
    op.add_column('tenants', sa.Column('bot_persona', sa.Text(), nullable=False, server_default=''))
    op.add_column('tenants', sa.Column('bot_tone', sa.String(20), nullable=False, server_default='neutral'))
    op.add_column('tenants', sa.Column('bot_rules', sa.Text(), nullable=False, server_default=''))
    op.add_column('tenants', sa.Column('business_hours', sa.Text(), nullable=False, server_default=''))
    op.add_column('tenants', sa.Column('timezone', sa.String(64), nullable=False, server_default='UTC'))
    op.add_column('tenants', sa.Column('default_language', sa.String(16), nullable=False, server_default='en'))
    op.add_column('tenants', sa.Column('bot_live', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    for name in ('bot_live', 'default_language', 'timezone', 'business_hours', 'bot_rules',
                 'bot_tone', 'bot_persona', 'bot_name'):
        op.drop_column('tenants', name)
