"""Session-bound Meta OAuth attempts with encrypted temporary credentials."""
import sqlalchemy as sa
from alembic import op

revision = '0054_meta_authorizations'
down_revision = '0053_meta_dm_binding'
branch_labels = depends_on = None


def upgrade():
    op.create_table('meta_authorizations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('session_hash', sa.String(64), sa.ForeignKey('user_sessions.id_hash', ondelete='CASCADE'), nullable=False),
        sa.Column('app_id', sa.String(64), nullable=False), sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('state_hash', sa.String(64), unique=True), sa.Column('status', sa.String(24), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False), sa.Column('secret_enc', sa.LargeBinary()),
        sa.Column('selected_id', sa.String(32)),
        sa.Column('channel_id', sa.String(36), sa.ForeignKey('channels.id', ondelete='SET NULL')),
        sa.Column('claim_token', sa.String(36)), sa.Column('claimed_at', sa.DateTime(timezone=True)),
        sa.Column('last_error', sa.String(64)), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_meta_authorizations_tenant_id', 'meta_authorizations', ['tenant_id'])
    op.create_index('ix_meta_authorizations_expires_at', 'meta_authorizations', ['expires_at'])
    op.create_index('ix_meta_authorizations_work', 'meta_authorizations', ['status', 'created_at'])


def downgrade():
    op.drop_table('meta_authorizations')
