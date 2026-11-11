"""Durable Google confirmation and event snapshots."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0039_calendar_bookings'
down_revision = '0038_calendar_availability'
branch_labels = None
depends_on = None


def upgrade():
    for column in (sa.Column('calendar_targets', JSONB, nullable=False, server_default='[]'),
                   sa.Column('calendar_state', sa.String(24), nullable=False, server_default='none'),
                   sa.Column('calendar_version', sa.Integer, nullable=False, server_default='0'),
                   sa.Column('calendar_error', sa.String(64))):
        op.add_column('appointment_bookings', column)
    for name in ('calendar_targets', 'calendar_state', 'calendar_version'):
        op.alter_column('appointment_bookings', name, server_default=None)
    op.drop_index('uq_appointment_slot_reserved', table_name='appointment_bookings')
    op.create_index('uq_appointment_slot_reserved', 'appointment_bookings', ['slot_id'], unique=True,
                    postgresql_where=sa.text("state IN ('held', 'confirming', 'confirmed')"))


def downgrade():
    # In-flight confirmations cannot be represented by the previous release.
    op.execute("UPDATE appointment_bookings SET state='expired' WHERE state='confirming'")
    op.drop_index('uq_appointment_slot_reserved', table_name='appointment_bookings')
    op.create_index('uq_appointment_slot_reserved', 'appointment_bookings', ['slot_id'], unique=True,
                    postgresql_where=sa.text("state IN ('held', 'confirmed')"))
    for name in ('calendar_error', 'calendar_version', 'calendar_state', 'calendar_targets'):
        op.drop_column('appointment_bookings', name)
