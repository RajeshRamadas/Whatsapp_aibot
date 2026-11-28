"""Store agency appearance and enforce exclusive verified hostnames."""
import sqlalchemy as sa
from alembic import op

revision = '0063_agency_branding'
down_revision = '0062_agency_billing'
branch_labels = depends_on = None


def upgrade():
    op.create_table('agency_brands',
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('display_name', sa.String(120), nullable=False),
        sa.Column('logo_url', sa.String(500)),
        sa.Column('primary_color', sa.String(7), nullable=False),
        sa.Column('secondary_color', sa.String(7), nullable=False),
        sa.Column('domain', sa.String(253)),
        sa.Column('challenge', sa.String(64)),
        sa.Column('verified_at', sa.DateTime(timezone=True)))
    op.create_index('uq_agency_verified_domain', 'agency_brands', ['domain'], unique=True,
                    postgresql_where=sa.text('verified_at IS NOT NULL'))


def downgrade():
    op.drop_table('agency_brands')
