"""Product-managed services, staff, slots and five-minute booking holds."""
import sqlalchemy as sa
from alembic import op

revision = '0036_appointments'
down_revision = '0035_bot_messages'
branch_labels = None
depends_on = None


def upgrade():
    def common():
        return [sa.Column('id', sa.String(36), primary_key=True),
                sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=False),
                sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
                sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)]
    op.create_table('appointment_services', *common(), sa.Column('name', sa.String(100), nullable=False),
        sa.Column('duration_minutes', sa.Integer, nullable=False), sa.Column('active', sa.Boolean, nullable=False),
        sa.Column('reminder_minutes', sa.Integer, nullable=False), sa.Column('reminder_template_id', sa.String(36),
            sa.ForeignKey('templates.id', ondelete='SET NULL')))
    op.create_table('appointment_staff', *common(), sa.Column('name', sa.String(100), nullable=False),
        sa.Column('active', sa.Boolean, nullable=False))
    op.create_table('appointment_slots', *common(),
        sa.Column('service_id', sa.String(36), sa.ForeignKey('appointment_services.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('staff_id', sa.String(36), sa.ForeignKey('appointment_staff.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('active', sa.Boolean, nullable=False))
    op.create_table('appointment_bookings', *common(),
        sa.Column('slot_id', sa.String(36), sa.ForeignKey('appointment_slots.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('customer_name', sa.String(100), nullable=False),
        sa.Column('customer_phone', sa.String(16), nullable=False),
        sa.Column('language', sa.String(16), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('replaces_id', sa.String(36), sa.ForeignKey('appointment_bookings.id', ondelete='SET NULL')),
        sa.Column('reminder_opt_in', sa.Boolean, nullable=False),
        sa.Column('reminder_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reminder_state', sa.String(16), nullable=False), sa.Column('reminder_error', sa.String(40)),
        sa.Column('reminder_message_id', sa.BigInteger, sa.ForeignKey('messages.id', ondelete='SET NULL')),
        sa.Column('claimed_at', sa.DateTime(timezone=True)))
    for table, columns in {'appointment_services': ['tenant_id'], 'appointment_staff': ['tenant_id'],
        'appointment_slots': ['tenant_id', 'staff_id', 'starts_at'],
        'appointment_bookings': ['tenant_id', 'conversation_id', 'reminder_at']}.items():
        for column in columns:
            op.create_index(f'ix_{table}_{column}', table, [column])
    op.create_index('uq_appointment_slot_reserved', 'appointment_bookings', ['slot_id'], unique=True,
                    postgresql_where=sa.text("state IN ('held', 'confirmed')"))


def downgrade():
    for table in ['appointment_bookings', 'appointment_slots', 'appointment_staff', 'appointment_services']:
        op.drop_table(table)
