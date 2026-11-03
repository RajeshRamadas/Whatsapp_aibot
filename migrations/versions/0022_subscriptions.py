"""Add Razorpay subscription state, trials and webhook deduplication."""
import sqlalchemy as sa
from alembic import op

revision = '0022_subscriptions'
down_revision = '0021_usage'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('trial_ends_at', sa.DateTime(timezone=True), nullable=False,
                                       server_default=sa.text("now() + interval '14 days'")))
    op.create_table(
        'subscriptions',
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('provider_id', sa.String(64), unique=True),
        sa.Column('checkout_key', sa.String(36), nullable=False),
        sa.Column('checkout_url', sa.Text()),
        sa.Column('plan_id', sa.String(20), sa.ForeignKey('plans.id'), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True)),
        sa.Column('grace_until', sa.DateTime(timezone=True)),
        sa.Column('reconcile_issue', sa.String(200)),
        sa.Column('settlement_issue', sa.String(200)),
        sa.Column('observed_at', sa.DateTime(timezone=True)),
    )
    op.create_table(
        'billing_events',
        sa.Column('id', sa.String(128), primary_key=True),
        sa.Column('event', sa.String(40), nullable=False),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE')),
        sa.Column('payment_id', sa.String(64)),
        sa.Column('amount_paise', sa.BigInteger()),
        sa.Column('settled_at', sa.DateTime(timezone=True)),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table('billing_events')
    op.drop_table('subscriptions')
    op.drop_column('tenants', 'trial_ends_at')
