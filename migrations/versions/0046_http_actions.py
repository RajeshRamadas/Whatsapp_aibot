"""Generic HTTP action definitions and encrypted call records."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0046_http_actions'
down_revision = '0045_outgoing_webhooks'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('http_actions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(32), nullable=False), sa.Column('definition', JSONB, nullable=False),
        sa.Column('credentials_enc', sa.LargeBinary, nullable=False),
        sa.Column('credential_last4', sa.String(4), nullable=False), sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('tested_revision', sa.Integer), sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('status', sa.String(24), nullable=False), sa.Column('last_success_at', sa.DateTime(timezone=True)),
        sa.Column('last_error', sa.String(40)), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_http_action_name'))
    op.create_table('http_action_calls',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action_id', sa.String(36), sa.ForeignKey('http_actions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='CASCADE')),
        sa.Column('revision', sa.Integer, nullable=False), sa.Column('dedupe_key', sa.String(64), nullable=False),
        sa.Column('inputs_enc', sa.LargeBinary), sa.Column('result_enc', sa.LargeBinary),
        sa.Column('state', sa.String(24), nullable=False), sa.Column('is_test', sa.Boolean, nullable=False),
        sa.Column('code_hash', sa.String(64)), sa.Column('code_attempts', sa.Integer, nullable=False),
        sa.Column('otp_verified', sa.Boolean, nullable=False), sa.Column('confirmed', sa.Boolean, nullable=False),
        sa.Column('http_status', sa.Integer), sa.Column('duration_ms', sa.Integer),
        sa.Column('last_error', sa.String(40)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'dedupe_key', name='uq_http_action_call'))
    for table, columns in {'http_actions': ['tenant_id'], 'http_action_calls': [
            'tenant_id', 'action_id', 'conversation_id', 'state', 'created_at']}.items():
        for column in columns:
            op.create_index(f'ix_{table}_{column}', table, [column])


def downgrade():
    op.drop_table('http_action_calls')
    op.drop_table('http_actions')
