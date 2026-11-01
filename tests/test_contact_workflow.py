"""Contact migration and message workflow integration tests."""
from sqlalchemy import select

from app import rag, whatsapp
from app.db import Contact, Conversation, Message, Session
from tests.conftest import wa_payload


def test_whatsapp_contact_is_reused_and_tenant_scoped(client, tenant):
    payload = whatsapp.parse_messages(wa_payload('hello'))[0]
    first = whatsapp.conversation_for(tenant['tenant_id'], payload['from'])
    second = whatsapp.conversation_for(tenant['tenant_id'], payload['from'])
    assert first.id == second.id and first.contact_id == second.contact_id
    with Session() as db:
        contact = db.get(Contact, first.contact_id)
        assert contact.tenant_id == tenant['tenant_id']
        assert contact.channel == 'whatsapp'
        assert contact.external_user == payload['from']
        assert db.scalar(select(Conversation).where(Conversation.id == first.id)).status == 'bot'


def test_message_metadata_and_notes_excluded_from_history(client, tenant):
    conv = whatsapp.conversation_for(tenant['tenant_id'], '15559998888')
    rag.save_exchange(conv, ('user', 'Hello'), ('assistant', 'Welcome'), ('note', 'Private escalation'))
    with Session() as db:
        messages = db.scalars(select(Message).where(Message.conversation_id == conv.id)
                              .order_by(Message.id)).all()
        assert [m.direction for m in messages] == ['inbound', 'outbound', 'outbound']
        assert [m.kind for m in messages] == ['text', 'text', 'note']
        saved = db.get(Conversation, conv.id)
        assert saved.last_inbound_at is not None and saved.last_message_at is not None
    assert rag.history(conv.id) == [{'role': 'user', 'content': 'Hello'},
                                    {'role': 'assistant', 'content': 'Welcome'}]


def test_handoff_status_tracks_legacy_flag(client, tenant):
    conv = whatsapp.conversation_for(tenant['tenant_id'], '15559997777')
    url = f'/api/v1/conversations/{conv.id}/handoff'
    assert client.post(url, headers=tenant['headers']).status_code == 200
    with Session() as db:
        row = db.get(Conversation, conv.id)
        assert row.status == 'agent' and row.handoff
    assert client.post(url + '?active=false', headers=tenant['headers']).status_code == 200
    with Session() as db:
        row = db.get(Conversation, conv.id)
        assert row.status == 'bot' and not row.handoff
