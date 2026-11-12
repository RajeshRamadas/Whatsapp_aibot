"""A supplied phone or leaked reference never releases another customer's order."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import llm, rag
from app.config import settings
from app.db import Conversation, Message, Session, Tenant, engine, now, uid
from app.integrations.commerce import Provider
from app.models.commerce import Store
from app.models.order_proofs import OrderProof
from app.services import email, order_lookup
from tests.test_commerce import store  # noqa: F401

DATA = {'number': '1001', 'status': 'PAID', 'fulfilment': 'FULFILLED',
        'items': [{'title': 'Private order item', 'quantity': 1}], 'tracking': []}


@pytest.fixture
def proof(tenant, store, monkeypatch):  # noqa: F811
    calls, sent = [], []
    async def order(self, number):
        assert engine.pool.checkedout() == 0
        calls.append(number)
        return {'phones': ['+91 (9999) 000001'], 'email': 'customer@example.com', **DATA}
    monkeypatch.setattr(Provider, 'order', order)
    monkeypatch.setattr(settings, 'email_backend', 'ses')
    monkeypatch.setattr(email.sender, 'stage', lambda db, *args: sent.append(args))
    with Session.begin() as db:
        conv = Conversation(id=uid(), tenant_id=tenant['tenant_id'], channel='web', status='bot')
        db.add(conv)
        identity = conv.id
    return store, identity, sent, calls


def test_web_proof_is_bound_expiring_once_and_encrypted(client, tenant, proof):
    identity, conv, sent, calls = proof
    tid = tenant['tenant_id']
    pending = order_lookup.lookup(tid, identity, conv, 'first', '1001', '919999000001')
    assert pending['state'] == 'awaiting_code' and 'data' not in pending
    assert order_lookup.lookup(tid, identity, conv, 'first', '1001') == pending
    assert len(sent) == len(calls) == 1
    assert sent[0][0] == 'customer@example.com'
    code = sent[0][2]
    with Session() as db:
        row = db.scalar(select(OrderProof).where(OrderProof.tenant_id == tid))
        assert b'Private order item' not in row.payload_enc and row.code_hash != code
    assert order_lookup.verify(tid, uid(), pending['reference'], code)['state'] == 'unavailable'
    wrong = '111111' if code != '111111' else '222222'
    assert order_lookup.verify(tid, conv, pending['reference'], wrong)['state'] == 'unavailable'
    assert order_lookup.verify(tid, conv, pending['reference'], code)['data'] == DATA
    assert order_lookup.verify(tid, conv, pending['reference'], code)['state'] == 'unavailable'
    with Session() as db:
        row = db.scalar(select(OrderProof).where(OrderProof.tenant_id == tid))
        assert row.used and row.payload_enc is None and row.code_hash is None


def test_verified_sender_cannot_be_overridden(client, tenant, proof):
    identity, conv, sent, _ = proof
    with Session.begin() as db:
        row = db.get(Conversation, conv)
        row.channel, row.external_user = 'whatsapp', '919999000001'
    result = order_lookup.lookup(tenant['tenant_id'], identity, conv, 'one', '1001', 'wrong-phone')
    assert result['data'] == DATA and not sent
    with Session.begin() as db:
        db.get(Conversation, conv).external_user = '919999000002'
    denied = order_lookup.lookup(tenant['tenant_id'], identity, conv, 'two', '1001', '919999000001')
    assert denied['state'] == 'awaiting_code' and 'data' not in denied


def test_expiry_lockout_disconnect_and_foreign_proof(client, tenant, proof):
    identity, conv, sent, _ = proof
    tid = tenant['tenant_id']
    first = order_lookup.lookup(tid, identity, conv, 'one', '1001')
    with Session.begin() as db:
        row = db.scalar(select(OrderProof).where(OrderProof.tenant_id == tid))
        row.expires_at = now() - timedelta(seconds=1)
    assert order_lookup.verify(tid, conv, first['reference'], sent[0][2])['state'] == 'unavailable'
    order_lookup.maintain()
    second = order_lookup.lookup(tid, identity, conv, 'two', '1001')
    code = sent[1][2]
    for _ in range(5):
        order_lookup.verify(tid, conv, second['reference'], '111111' if code != '111111' else '222222')
    assert order_lookup.verify(tid, conv, second['reference'], code)['state'] == 'unavailable'
    third = order_lookup.lookup(tid, identity, conv, 'three', '1001')
    assert order_lookup.verify(uid(), conv, third['reference'], sent[2][2])['state'] == 'unavailable'
    with Session.begin() as db:
        db.get(Store, identity).status = 'needs_test'
    assert order_lookup.verify(tid, conv, third['reference'], sent[2][2])['state'] == 'unavailable'
    with Session.begin() as db:
        db.get(Store, identity).status = 'connected'
        db.get(Conversation, conv).handoff = True
    assert order_lookup.verify(tid, conv, third['reference'], sent[2][2])['state'] == 'unavailable'


def test_literal_code_bypasses_model_and_transcript_masks_it(client, tenant, proof, chats):
    identity, conv, sent, _ = proof
    tid = tenant['tenant_id']
    pending = order_lookup.lookup(tid, identity, conv, 'one', '1001')
    command = 'ORDER ' + pending['reference'] + ' ' + sent[0][2]
    with Session() as db:
        tenant_row = db.get(Tenant, tid)
    result = rag.answer(tenant_row, conv, command, action_turn_key='proof-turn')
    assert 'Private order item' in result.text and result.transactional
    assert not chats['openai'].calls
    with Session.begin() as db:
        row = db.get(Conversation, conv)
        rag.save_exchange_rows(db, row, [('user', command), ('assistant', result.text)])
    with Session() as db:
        text = db.scalar(select(Message.content).where(Message.conversation_id == conv, Message.role == 'user'))
    assert sent[0][2] not in text and '[redacted]' in text


def test_web_tool_requires_proof_and_verified_order_is_grounded(client, tenant, proof, chats, monkeypatch):
    _, conv, sent, _ = proof
    with Session() as db:
        workspace = db.get(Tenant, tenant['tenant_id'])
    def run(model, system, messages, tools):
        tool = next(t for t in tools if t.name == 'lookup_order')
        response = tool.handler({'order_number': '1001', 'phone': '919999000001'})
        if response['state'] == 'unavailable':
            return llm.ChatResult('A malicious invented order claim', [])
        assert 'email' not in response['untrusted_data'] and 'phones' not in response['untrusted_data']
        return llm.ChatResult('Order 1001 is PAID [' + str(response['source']['number']) + ']', [])
    monkeypatch.setattr(chats['openai'], 'run', run)
    pending = rag.answer(workspace, conv, 'Check order 1001', action_turn_key='one')
    assert 'one-time code' in pending.text and 'malicious' not in pending.text and sent
    with Session.begin() as db:
        row = db.get(Conversation, conv)
        row.channel, row.external_user = 'whatsapp', '919999000001'
    verified = rag.answer(workspace, conv, 'Check order 1001', action_turn_key='two')
    assert verified.grounded and verified.sources[0]['filename'] == 'Order status'
    chats['openai'].grounding_verdict = {'verdict': 'unsupported', 'citations': []}
    assert rag.answer(workspace, conv, 'Check order 1001', action_turn_key='three').grounding_blocked


def test_console_delivery_and_provider_failure_reveal_nothing(client, tenant, proof, monkeypatch):
    identity, conv, sent, _ = proof
    monkeypatch.setattr(settings, 'email_backend', 'console')
    assert order_lookup.lookup(tenant['tenant_id'], identity, conv, 'one', '1001') == {'state': 'unavailable'}
    assert not sent
    async def fail(self, number):
        raise TimeoutError('sensitive provider data')
    monkeypatch.setattr(Provider, 'order', fail)
    assert order_lookup.lookup(tenant['tenant_id'], identity, conv, 'two', '1001') == {'state': 'unavailable'}
