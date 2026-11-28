"""Remember the industry template used at workspace creation."""
import sqlalchemy as sa
from alembic import op

revision = '0059_industry_templates'
down_revision = '0058_meta_token_health'
branch_labels = depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('industry_template', sa.String(64)))


def downgrade():
    op.drop_column('tenants', 'industry_template')
