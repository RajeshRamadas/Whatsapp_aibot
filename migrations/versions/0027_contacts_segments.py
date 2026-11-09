"""Consent provenance and tenant-scoped contact segments."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0027_contacts_segments'
down_revision = '0026_email_delivery'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('contacts', sa.Column('consent_source', sa.String(120)))
    op.create_table('contact_segments',
                    sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                              nullable=False),
                    sa.Column('name', sa.String(100), nullable=False),
                    sa.Column('filters', postgresql.JSONB(), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
                    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
                    sa.UniqueConstraint('tenant_id', 'name'))
    op.create_index('ix_contact_segments_tenant_id', 'contact_segments', ['tenant_id'])


def downgrade():
    op.drop_table('contact_segments')
    op.drop_column('contacts', 'consent_source')
