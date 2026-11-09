"""Encrypted transactional outbox and privacy-limited email suppression metadata."""
import sqlalchemy as sa
from alembic import op

revision = '0026_email_delivery'
down_revision = '0025_analytics'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('email_outbox',
                    sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('recipient_hash', sa.String(64), nullable=False),
                    sa.Column('payload_enc', sa.LargeBinary()),
                    sa.Column('status', sa.String(16), nullable=False),
                    sa.Column('attempts', sa.Integer(), nullable=False),
                    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
                    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
                    sa.Column('provider_id', sa.String(128)))
    op.create_index('ix_email_outbox_next_attempt_at', 'email_outbox', ['next_attempt_at'])
    op.create_table('email_suppressions',
                    sa.Column('recipient_hash', sa.String(64), primary_key=True),
                    sa.Column('reason', sa.String(16), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('email_feedback_events',
                    sa.Column('id', sa.String(150), primary_key=True),
                    sa.Column('kind', sa.String(32), nullable=False),
                    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('email_feedback_events')
    op.drop_table('email_suppressions')
    op.drop_table('email_outbox')
