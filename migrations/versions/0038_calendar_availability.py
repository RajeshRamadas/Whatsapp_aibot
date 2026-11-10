"""Calendar resource bindings, availability cache and derived slots."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0038_calendar_availability'
down_revision = '0037_google_calendar'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('google_calendar_connections', sa.Column('busy', JSONB, nullable=False, server_default='{}'))
    op.add_column('appointment_slots', sa.Column('calendar_signature', sa.String(64)))
    op.create_table('google_calendar_bindings', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('connection_id', sa.String(36),
                  sa.ForeignKey('google_calendar_connections.id', ondelete='CASCADE'), nullable=False),
        sa.Column('calendar_id', sa.String(1024), nullable=False),
        sa.Column('service_id', sa.String(36), sa.ForeignKey('appointment_services.id', ondelete='CASCADE')),
        sa.Column('staff_id', sa.String(36), sa.ForeignKey('appointment_staff.id', ondelete='CASCADE')))
    op.create_index('ix_google_calendar_bindings_tenant_id', 'google_calendar_bindings', ['tenant_id'])
    op.alter_column('google_calendar_connections', 'busy', server_default=None)


def downgrade():
    op.drop_table('google_calendar_bindings')
    op.drop_column('appointment_slots', 'calendar_signature')
    op.drop_column('google_calendar_connections', 'busy')
