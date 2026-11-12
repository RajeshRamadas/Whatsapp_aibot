"""Selected Drive folders and Notion pages with linked source provenance."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0051_knowledge_sync'
down_revision = '0050_order_proofs'
branch_labels = depends_on = None


def upgrade():
    op.create_table('knowledge_connections',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String(12), nullable=False), sa.Column('label', sa.String(100), nullable=False),
        sa.Column('selection', JSONB, nullable=False), sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('status', sa.String(24), nullable=False), sa.Column('credentials_enc', sa.LargeBinary),
        sa.Column('oauth_state_hash', sa.String(64), unique=True), sa.Column('oauth_session_hash', sa.String(64)),
        sa.Column('oauth_user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('oauth_expires_at', sa.DateTime(timezone=True)), sa.Column('oauth_secret_enc', sa.LargeBinary),
        sa.Column('cursor', sa.String(2048)), sa.Column('lease', sa.String(36)),
        sa.Column('next_run', sa.DateTime(timezone=True)), sa.Column('attempts', sa.Integer, nullable=False),
        sa.Column('last_success', sa.DateTime(timezone=True)), sa.Column('last_error', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'provider', name='uq_knowledge_provider'))
    for key in ('tenant_id', 'next_run'):
        op.create_index('ix_knowledge_connections_' + key, 'knowledge_connections', [key])
    op.add_column('sources', sa.Column('sync_connection_id', sa.String(36)))
    op.create_foreign_key('fk_sources_sync', 'sources', 'knowledge_connections', ['sync_connection_id'], ['id'],
                          ondelete='CASCADE')
    op.create_index('ix_sources_sync_connection_id', 'sources', ['sync_connection_id'])
    op.add_column('sources', sa.Column('sync_external_id', sa.String(128)))
    op.add_column('sources', sa.Column('sync_version', sa.String(200)))
    op.create_unique_constraint('uq_sync_source', 'sources', ['sync_connection_id', 'sync_external_id'])


def downgrade():
    op.execute('DELETE FROM sources WHERE sync_connection_id IS NOT NULL')
    op.drop_constraint('uq_sync_source', 'sources')
    op.drop_column('sources', 'sync_version')
    op.drop_column('sources', 'sync_external_id')
    op.drop_index('ix_sources_sync_connection_id', 'sources')
    op.drop_constraint('fk_sources_sync', 'sources')
    op.drop_column('sources', 'sync_connection_id')
    op.drop_table('knowledge_connections')
