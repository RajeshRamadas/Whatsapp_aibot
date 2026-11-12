"""Conversation-bound one-time proof for personal order data."""
import sqlalchemy as sa
from alembic import op

revision = '0050_order_proofs'
down_revision = '0049_commerce'
branch_labels = depends_on = None


def upgrade():
    op.create_table('order_proofs', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('connection_id', sa.String(36), sa.ForeignKey('commerce_stores.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('dedupe_key', sa.String(64), nullable=False), sa.Column('payload_enc', sa.LargeBinary),
        sa.Column('code_hash', sa.String(64)), sa.Column('attempts', sa.Integer, nullable=False),
        sa.Column('used', sa.Boolean, nullable=False), sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'dedupe_key', name='uq_order_proof_turn'))
    for name in ('tenant_id', 'expires_at'):
        op.create_index('ix_order_proofs_' + name, 'order_proofs', [name])


def downgrade():
    op.drop_table('order_proofs')
