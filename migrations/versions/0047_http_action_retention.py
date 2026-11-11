"""Index only active payloads for the frequent action-retention sweep."""
import sqlalchemy as sa
from alembic import op

revision = '0047_http_action_retention'
down_revision = '0046_http_actions'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_http_action_calls_expiring', 'http_action_calls', ['expires_at'],
        postgresql_where=sa.text('inputs_enc IS NOT NULL OR result_enc IS NOT NULL OR code_hash IS NOT NULL'))


def downgrade():
    op.drop_index('ix_http_action_calls_expiring', table_name='http_action_calls')
