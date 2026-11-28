"""Add bounded, operator-bound support grants."""
import sqlalchemy as sa
from alembic import op

revision = '0061_support_sessions'
down_revision = '0060_lead_follow_ups'
branch_labels = depends_on = None


def upgrade():
    op.create_table('support_sessions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('user_session_id', sa.String(64), sa.ForeignKey('user_sessions.id_hash', ondelete='SET NULL')),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)))
    op.create_index('ix_support_sessions_user_session_id', 'support_sessions', ['user_session_id'])
    op.create_index('ix_support_sessions_expires_at', 'support_sessions', ['expires_at'])


def downgrade():
    op.drop_table('support_sessions')
