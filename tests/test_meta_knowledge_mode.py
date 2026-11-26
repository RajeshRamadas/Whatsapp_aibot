"""Meta uses a restricted knowledge surface until transactional channels are supported."""
import pytest

from app import llm, rag
from app.db import Conversation, Message, Session, Tenant, engine, uid
from tests.test_api import upload


@pytest.mark.parametrize('question', ['refund policy', 'CANCEL abcdef123456', 'VERIFY abcdef 123456'])
def test_knowledge_mode_skips_mutating_commands_and_tools(client, tenant, chats, monkeypatch, question):
    upload(client, tenant, 'policy.txt', 'Refund policy: refunds within 30 days.')
    with Session.begin() as db:
        workspace = db.get(Tenant, tenant['tenant_id'])
        workspace.lead_fields = {'email': 'required'}
    def forbidden(*args, **kwargs):
        raise AssertionError('A restricted Meta turn must not enter transactional integrations')
    for module in (rag.appointment_chat, rag.http_action_chat, rag.order_notices):
        monkeypatch.setattr(module, 'command', forbidden)
    for module in (rag.appointment_chat, rag.payment_chat, rag.http_action_chat, rag.commerce_chat):
        monkeypatch.setattr(module, 'tools', forbidden)
    def run(model, system, messages, tools):
        assert engine.pool.checkedout() == 0
        assert {tool.name for tool in tools} == {'offer_handoff'}
        assert 'knowledge text answers only' in system
        return llm.ChatResult('Refunds within 30 days. [1]', [])
    monkeypatch.setattr(chats['openai'], 'run', run)
    result = rag.answer(workspace, None, 'refund ' + question, knowledge_only=True)
    assert result.grounded and result.sources and not result.transactional


def test_pre_recorded_turn_excludes_itself_and_newer_messages_from_history(client, tenant, chats, monkeypatch):
    with Session.begin() as db:
        conv = Conversation(id=uid(), tenant_id=tenant['tenant_id'], channel='instagram', external_user='customer')
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, role='user', content='older question'))
        db.flush()
        current = Message(conversation_id=conv.id, role='user', content='current question')
        db.add(current)
        db.flush()
        cutoff = current.id
        db.add(Message(conversation_id=conv.id, role='user', content='newer question'))
    turns = rag.history(conv.id, cutoff)
    assert turns == [{'role': 'user', 'content': 'older question'}]
    assert len(rag.history(conv.id)) == 3
