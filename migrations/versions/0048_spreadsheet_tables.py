"""Metadata for typed spreadsheet tables and isolated read-only query roles."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0048_spreadsheet_tables'
down_revision = '0047_http_action_retention'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('spreadsheet_tables',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_id', sa.String(36), sa.ForeignKey('sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer, nullable=False), sa.Column('title', sa.String(200), nullable=False),
        sa.Column('table_name', sa.String(50), nullable=False), sa.Column('columns', JSONB, nullable=False),
        sa.Column('row_count', sa.Integer, nullable=False), sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('source_id', 'position', name='uq_spreadsheet_position'))
    for column in ('tenant_id', 'source_id'):
        op.create_index(f'ix_spreadsheet_tables_{column}', 'spreadsheet_tables', [column])


def downgrade():
    from app.spreadsheets.store import cleanup
    cleanup(reset=True)
    op.drop_table('spreadsheet_tables')
