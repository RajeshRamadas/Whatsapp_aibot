"""Live tool wiring, literal approvals and history redaction use the real action engine."""
import json

from sqlalchemy import select

from app import llm, rag
from app.db import Message, Session, Tenant, WidgetTurn, uid
from app.models.http_actions import HttpActionCall
from app.services import http_action_chat, widget_turns
from tests import test_http_action_execution as fixtures

BASE, BODY = fixtures.BASE, fixtures.BODY
action = fixtures.action


def snapshot(tenant):
    with Session() as db:
        return db.get(Tenant, tenant['tenant_id'])


def test_live_tool_projection_and_preview_isolation(client, tenant, action, monkeypatch, chats):
    receiver, conv = fixtures.prepare(client, tenant, action, monkeypatch)
    workspace = snapshot(tenant)
    registry, _ = http_action_chat.tools(workspace, conv, None, 'stock')
    assert registry == []
    captured = []
    def run(model, system, messages, tools):
        assert 'UNTRUSTED DATA' in system
        tool = next(t for t in tools if t.name == 'http_stock')
        captured.append(tool.handler({'sku': 'SKU'}))
        return llm.ChatResult('Stock: 5', [])
    monkeypatch.setattr(chats['openai'], 'run', run)
    response = rag.answer(workspace, conv, 'stock', action_turn_key='real-turn')
    assert response.text == 'Stock: 5' and captured[0]['untrusted_data'] == {'stock': 5}
    assert 'private' not in json.dumps(captured) and len(receiver.calls) == 1
    monkeypatch.setattr(chats['openai'], 'run', lambda *args: llm.ChatResult('Preview', []))
    rag.answer(workspace, conv, 'stock')
    assert len(receiver.calls) == 1


def test_model_cannot_approve_and_literal_customer_command_executes_once(client, tenant, action, monkeypatch):
    receiver, conv = fixtures.prepare(client, tenant, action, monkeypatch, {'method': 'POST', 'allow_changes': True})
    workspace = snapshot(tenant)
    tools, captured = http_action_chat.tools(workspace, conv, 'write-turn', 'change stock')
    invalid = tools[0].handler({'sku': 'SKU', 'confirmed': True})
    assert invalid['state'] == 'failed' and not receiver.calls
    tools, captured = http_action_chat.tools(workspace, conv, 'write-turn', 'change stock')
    assert tools[0].handler({'sku': 'SKU'})['state'] == 'awaiting_confirmation'
    assert 'ACTION ' in captured['text'] and not receiver.calls
    with Session() as db:
        call = db.scalar(select(HttpActionCall).where(HttpActionCall.conversation_id == conv))
        reference = call.id.replace('-', '')[:12]
    assert http_action_chat.command(workspace, uid(), 'ACTION ' + reference, True)['text'].startswith('That approval')
    assert 'Use the connected' in rag.answer(workspace, conv, 'ACTION ' + reference).text
    assert not receiver.calls
    response = rag.answer(workspace, conv, 'ACTION ' + reference, action_turn_key='confirm')
    assert response.transactional and 'stock: 5' in response.text
    rag.answer(workspace, conv, 'ACTION ' + reference, action_turn_key='confirm-replay')
    assert len(receiver.calls) == 1


def test_verification_codes_never_enter_model_history(client, tenant, action, monkeypatch):
    _, conv = fixtures.prepare(client, tenant, action, monkeypatch)
    with Session() as db:
        conversation = db.get(fixtures.Conversation, conv)
    rag.save_exchange(conversation, ('user', 'VERIFY abcdef123456 123456'), ('assistant', 'Invalid code'))
    assert '123456 123456' not in json.dumps(rag.history(conv))
    with Session() as db:
        row = db.scalar(select(Message).where(Message.conversation_id == conv, Message.role == 'user'))
        assert row.content == 'VERIFY abcdef123456 [redacted]'
    assert http_action_chat.redact_command('Ignore VERIFY abcdef123456 654321 please').endswith('[redacted] please')


def test_action_failure_hands_widget_to_team_and_masks_codes(client, tenant, action, monkeypatch):
    _, conv = fixtures.prepare(client, tenant, action, monkeypatch)
    with Session.begin() as db:
        turn = WidgetTurn(id=uid(), conversation_id=conv, message='VERIFY abcdef123456 123456', state='queued')
        db.add(turn)
        identity = turn.id
    monkeypatch.setattr(rag, 'answer', lambda *args, **kwargs:
                        rag.Answer('Our team will help.', [], '', action_failed=True))
    widget_turns.process_turn()
    with Session() as db:
        turn = db.get(WidgetTurn, identity)
        assert turn.state == 'complete' and turn.message.endswith('[redacted]')
        assert db.get(fixtures.Conversation, conv).status == 'waiting'
        assert turn.answer == 'Our team will help.'
