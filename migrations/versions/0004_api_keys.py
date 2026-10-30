"""Scoped API keys; migrate legacy admin hashes without exposing plaintext."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0004_api_keys'
down_revision = '0003_workspaces_roles'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'api_keys',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('key_prefix', sa.String(24), nullable=False),
        sa.Column('key_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('scopes', JSONB, nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True)),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.Column('expires_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_api_keys_tenant_id', 'api_keys', ['tenant_id'])
    op.execute(sa.text("""INSERT INTO api_keys
        (id, tenant_id, name, key_prefix, key_hash, scopes)
        SELECT CAST(gen_random_uuid() AS varchar), id, 'Legacy admin key', 'adm_', admin_key_hash,
               CAST('["owner"]' AS jsonb)
        FROM tenants"""))


def downgrade():
    op.drop_table('api_keys')
