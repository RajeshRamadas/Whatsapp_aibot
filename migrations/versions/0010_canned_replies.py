"""Workspace canned replies."""
import sqlalchemy as sa
from alembic import op

revision = '0010_canned_replies'
down_revision = '0009_bot_settings'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'canned_replies',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(100), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_canned_replies_tenant_id', 'canned_replies', ['tenant_id'])


def downgrade():
    op.drop_index('ix_canned_replies_tenant_id', table_name='canned_replies')
    op.drop_table('canned_replies')
