"""Workspace memberships and expiring invitations."""
import sqlalchemy as sa
from alembic import op

revision = '0003_workspaces_roles'
down_revision = '0002_users_sessions'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'memberships',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.UniqueConstraint('user_id', 'tenant_id'),
    )
    op.create_index('ix_memberships_user_id', 'memberships', ['user_id'])
    op.create_index('ix_memberships_tenant_id', 'memberships', ['tenant_id'])
    op.create_table(
        'invitations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('invited_by', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
    )
    op.create_index('ix_invitations_tenant_id', 'invitations', ['tenant_id'])


def downgrade():
    op.drop_table('invitations')
    op.drop_table('memberships')
