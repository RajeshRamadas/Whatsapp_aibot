"""Seed plan limits and add usage accounting."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0021_usage'
down_revision = '0020_widget'
branch_labels = None
depends_on = None


def upgrade():
    plans = op.create_table('plans', sa.Column('id', sa.String(20), primary_key=True),
                            sa.Column('limits', JSONB, nullable=False))
    op.bulk_insert(plans, [
        {'id': 'starter', 'limits': {'ai_messages': 1000, 'channels': 2, 'seats': 2, 'files': 50,
                                     'price_inr': 1999, 'overage_inr': 1}},
        {'id': 'growth', 'limits': {'ai_messages': 5000, 'channels': 3, 'seats': 5, 'files': 500,
                                    'price_inr': 5999, 'overage_inr': 1}},
        {'id': 'business', 'limits': {'ai_messages': 20000, 'channels': 5, 'seats': 15, 'files': None,
                                       'price_inr': 14999, 'overage_inr': 1}},
    ])
    op.add_column('tenants', sa.Column('plan_id', sa.String(20), sa.ForeignKey('plans.id'),
                                      nullable=False, server_default='starter'))
    op.add_column('tenants', sa.Column('quota_behavior', sa.String(16), nullable=False, server_default='pause'))
    op.create_table(
        'usage_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('request_key', sa.String(150)),
        sa.Column('provider', sa.String(32)),
        sa.Column('model', sa.String(200)),
        sa.Column('input_tokens', sa.BigInteger()),
        sa.Column('output_tokens', sa.BigInteger()),
        sa.Column('cost_usd', sa.Numeric(20, 10)),
        sa.Column('overage', sa.Boolean(), nullable=False),
        sa.Column('notified_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'request_key', name='uq_usage_request'),
    )
    op.create_index('ix_usage_tenant_kind_created', 'usage_events', ['tenant_id', 'kind', 'created_at'])
    op.create_table(
        'usage_daily',
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('day', sa.Date(), primary_key=True),
        sa.Column('ai_messages', sa.BigInteger(), nullable=False),
        sa.Column('overage_messages', sa.BigInteger(), nullable=False),
        sa.Column('ai_calls', sa.BigInteger(), nullable=False),
        sa.Column('input_tokens', sa.BigInteger(), nullable=False),
        sa.Column('output_tokens', sa.BigInteger(), nullable=False),
        sa.Column('cost_usd', sa.Numeric(20, 10), nullable=False),
        sa.Column('unpriced_calls', sa.BigInteger(), nullable=False),
    )


def downgrade():
    op.drop_table('usage_daily')
    op.drop_table('usage_events')
    op.drop_column('tenants', 'quota_behavior')
    op.drop_column('tenants', 'plan_id')
    op.drop_table('plans')
