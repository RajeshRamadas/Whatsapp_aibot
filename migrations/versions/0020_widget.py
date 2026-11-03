"""Add widget themes and durable web turns."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0020_widget'
down_revision = '0019_templates'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('widget_theme', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.alter_column('tenants', 'widget_theme', server_default=None)
    op.create_table(
        'widget_turns',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('handoff', sa.Boolean(), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('answer', sa.Text()),
        sa.Column('sources', JSONB, nullable=False),
        sa.Column('feedback', sa.String(4)),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_widget_turns_conversation_id', 'widget_turns', ['conversation_id'])


def downgrade():
    op.drop_table('widget_turns')
    op.drop_column('tenants', 'widget_theme')
