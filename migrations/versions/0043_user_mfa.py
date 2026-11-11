"""Account-wide optional TOTP and second-factor session assurance."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0043_user_mfa'
down_revision = '0042_eval_runs'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user_sessions', sa.Column('mfa_verified', sa.Boolean, nullable=False, server_default=sa.false()))
    op.alter_column('user_sessions', 'mfa_verified', server_default=None)
    op.create_table('user_mfa', sa.Column('user_id', sa.String(36),
        sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('secret_enc', sa.LargeBinary), sa.Column('enabled_at', sa.DateTime(timezone=True)),
        sa.Column('version', sa.Integer, nullable=False), sa.Column('last_step', sa.BigInteger, nullable=False),
        sa.Column('recovery_hashes', JSONB, nullable=False), sa.Column('pending_enc', sa.LargeBinary),
        sa.Column('pending_expires_at', sa.DateTime(timezone=True)), sa.Column('pending_session', sa.String(64)),
        sa.Column('pending_password', sa.String(64)), sa.Column('pending_attempts', sa.Integer, nullable=False))
    op.create_table('mfa_challenges', sa.Column('id_hash', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('csrf_hash', sa.String(64), nullable=False), sa.Column('password_digest', sa.String(64), nullable=False),
        sa.Column('version', sa.Integer, nullable=False), sa.Column('attempts', sa.Integer, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_mfa_challenges_user_id', 'mfa_challenges', ['user_id'])
    op.create_index('ix_mfa_challenges_expires_at', 'mfa_challenges', ['expires_at'])


def downgrade():
    op.drop_table('mfa_challenges')
    op.drop_table('user_mfa')
    op.drop_column('user_sessions', 'mfa_verified')
