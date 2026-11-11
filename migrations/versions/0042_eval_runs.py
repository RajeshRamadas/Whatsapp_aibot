"""Durable accuracy-run snapshots, progress and history."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0042_eval_runs'
down_revision = '0041_lead_sync'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('eval_runs', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_key', sa.String(36), nullable=False), sa.Column('label', sa.String(100), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        *[sa.Column(k, JSONB, nullable=False) for k in ('cases', 'context', 'results')],
        *[sa.Column(k, sa.Integer, nullable=False) for k in ('total', 'completed', 'passed', 'errors')],
        *[sa.Column(k, sa.Float) for k in ('accuracy', 'source_hit_rate')], sa.Column('last_error', sa.String(64)),
        *[sa.Column(k, sa.DateTime(timezone=True)) for k in ('claimed_at', 'started_at', 'finished_at')],
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'request_key', name='uq_eval_run_request'))
    op.create_index('ix_eval_runs_tenant_id', 'eval_runs', ['tenant_id'])
    op.create_index('ix_eval_runs_state', 'eval_runs', ['state'])


def downgrade():
    op.drop_table('eval_runs')
