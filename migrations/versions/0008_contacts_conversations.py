"""Contacts and conversation/message workflow metadata."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0008_contacts_conversations'
down_revision = '0007_inbound_events'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('contacts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel', sa.String(20), nullable=False),
        sa.Column('external_user', sa.String(64), nullable=False),
        sa.Column('name', sa.String(200)),
        sa.Column('language', sa.String(20)),
        sa.Column('opt_in_at', sa.DateTime(timezone=True)),
        sa.Column('opt_out_at', sa.DateTime(timezone=True)),
        sa.Column('attributes', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint('tenant_id', 'channel', 'external_user'),
    )
    op.create_index('ix_contacts_tenant_id', 'contacts', ['tenant_id'])
    op.add_column('conversations', sa.Column('contact_id', sa.String(36),
                                            sa.ForeignKey('contacts.id', ondelete='SET NULL')))
    op.add_column('conversations', sa.Column('status', sa.String(20), nullable=False,
                                            server_default='bot'))
    op.add_column('conversations', sa.Column('assignee_user_id', sa.String(36),
                                            sa.ForeignKey('users.id', ondelete='SET NULL')))
    op.add_column('conversations', sa.Column('labels', JSONB, nullable=False,
                                            server_default=sa.text("'[]'::jsonb")))
    op.add_column('conversations', sa.Column('last_inbound_at', sa.DateTime(timezone=True)))
    op.add_column('conversations', sa.Column('last_message_at', sa.DateTime(timezone=True)))
    op.execute("""INSERT INTO contacts (id, tenant_id, channel, external_user, attributes)
        SELECT md5(tenant_id || ':' || channel || ':' || external_user),
               tenant_id, channel, external_user, '{}'::jsonb
        FROM conversations WHERE external_user IS NOT NULL
        GROUP BY tenant_id, channel, external_user""")
    op.execute("""UPDATE conversations c SET contact_id = ct.id FROM contacts ct
        WHERE c.tenant_id = ct.tenant_id AND c.channel = ct.channel
        AND c.external_user = ct.external_user""")
    op.execute("UPDATE conversations SET status = 'agent' WHERE handoff = true")
    op.execute("""UPDATE conversations c SET last_message_at = (
        SELECT max(m.created_at) FROM messages m WHERE m.conversation_id = c.id)""")
    op.execute("""UPDATE conversations c SET last_inbound_at = (
        SELECT max(m.created_at) FROM messages m
        WHERE m.conversation_id = c.id AND m.role = 'user')""")
    op.add_column('messages', sa.Column('direction', sa.String(12), nullable=False,
                                        server_default='inbound'))
    op.add_column('messages', sa.Column('author_user_id', sa.String(36),
                                        sa.ForeignKey('users.id', ondelete='SET NULL')))
    op.add_column('messages', sa.Column('kind', sa.String(20), nullable=False, server_default='text'))
    op.add_column('messages', sa.Column('channel_message_id', sa.String(128)))
    op.add_column('messages', sa.Column('delivery_status', sa.String(20)))
    op.add_column('messages', sa.Column('error', sa.Text()))
    op.execute("""UPDATE messages SET direction = 'outbound' WHERE role IN ('assistant', 'agent')""")


def downgrade():
    op.execute("UPDATE conversations SET handoff = (status IN ('agent', 'waiting'))")
    for column in ('error', 'delivery_status', 'channel_message_id', 'kind',
                   'author_user_id', 'direction'):
        op.drop_column('messages', column)
    for column in ('last_message_at', 'last_inbound_at', 'labels',
                   'assignee_user_id', 'status', 'contact_id'):
        op.drop_column('conversations', column)
    op.drop_table('contacts')
