"""Users, sessions and one-time verification codes."""
import sqlalchemy as sa
from alembic import op

revision = '0002_users_sessions'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('email', sa.String(320), nullable=False, unique=True),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('email_verified_at', sa.DateTime(timezone=True)),
        sa.Column('is_platform_admin', sa.Boolean(), nullable=False))
    op.create_table('user_sessions',
        sa.Column('id_hash', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('csrf_hash', sa.String(64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ip', sa.String(64)),
        sa.Column('user_agent', sa.String(512)))
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'])
    op.create_table('auth_codes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('purpose', sa.String(20), nullable=False),
        sa.Column('code_hash', sa.String(64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_auth_codes_user_id', 'auth_codes', ['user_id'])


def downgrade():
    op.drop_table('auth_codes')
    op.drop_table('user_sessions')
    op.drop_table('users')
