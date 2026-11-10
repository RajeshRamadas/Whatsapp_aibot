"""Durable website sources, schedules and per-plan page budgets."""
import sqlalchemy as sa
from alembic import op

revision = '0033_websites'
down_revision = '0032_ocr_usage'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('websites',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('url', sa.Text(), nullable=False), sa.Column('max_pages', sa.Integer(), nullable=False),
        sa.Column('depth', sa.Integer(), nullable=False), sa.Column('schedule', sa.String(12), nullable=False),
        sa.Column('status', sa.String(16), nullable=False), sa.Column('error', sa.Text()),
        sa.Column('attempts', sa.Integer(), nullable=False), sa.Column('generation', sa.String(36)),
        sa.Column('started_at', sa.DateTime(timezone=True)), sa.Column('next_run', sa.DateTime(timezone=True)),
        sa.Column('last_success', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'url', name='uq_website_url'))
    op.create_index('ix_websites_tenant_id', 'websites', ['tenant_id'])
    op.create_index('ix_websites_next_run', 'websites', ['next_run'])
    op.add_column('sources', sa.Column('website_id', sa.String(36),
                  sa.ForeignKey('websites.id', ondelete='CASCADE')))
    op.add_column('sources', sa.Column('url', sa.Text()))
    op.add_column('sources', sa.Column('checksum', sa.String(64)))
    op.add_column('sources', sa.Column('crawl_depth', sa.Integer()))
    op.create_index('ix_sources_website_id', 'sources', ['website_id'])
    op.create_unique_constraint('uq_website_page', 'sources', ['website_id', 'url'])
    for plan, limit in [('starter', 0), ('growth', 50), ('business', 200)]:
        op.execute(sa.text("UPDATE plans SET limits=limits || jsonb_build_object('crawl_pages', :limit) "
                           "WHERE id=:plan AND NOT limits ? 'crawl_pages'").bindparams(plan=plan, limit=limit))


def downgrade():
    op.drop_constraint('uq_website_page', 'sources')
    op.drop_index('ix_sources_website_id', 'sources')
    for name in ('crawl_depth', 'checksum', 'url', 'website_id'):
        op.drop_column('sources', name)
    op.drop_table('websites')
