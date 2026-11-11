"""Grounding failures replace drafts across delivery paths without replaying actions."""
import json

import pytest
from sqlalchemy import select

from app import llm, rag
from app.config import settings
from app.db import AnswerReview, AuditLog, Conversation, Message, Session, Tenant, engine, uid
from app.models.http_actions import HttpActionCall
from app.services.customer_languages import COPY
from app.services.whatsapp_copy import format_reply
from app.services.widget_turns import process_turn
from tests import test_http_action_execution as actions
from tests.conftest import wa_payload
from tests.test_api import upload
from tests.test_spreadsheet_import import tables
from tests.test_spreadsheet_import import upload as sheet_upload
from tests.test_whatsapp import link_number, post

action = actions.action


def workspace(tenant):
    with Session() as db:
        return db.get(Tenant, tenant['tenant_id'])


def draft(chats, monkeypatch, text):
    monkeypatch.setattr(chats['openai'], 'run', lambda *args: llm.ChatResult(text, []))


def test_verified_facts_keep_only_their_citations(client, tenant, chats, monkeypatch):
    upload(client, tenant, 'first.txt', 'Refunds within 30 days.')
    upload(client, tenant, 'second.txt', 'Refunds within 30 days. Additional details.')
    draft(chats, monkeypatch, 'Refunds within 30 days. [1]')
    original = chats['openai'].verify
    def verify(*args):
        assert engine.pool.checkedout() == 0
        return original(*args)
    monkeypatch.setattr(chats['openai'], 'verify', verify)
    result = rag.answer(workspace(tenant), None, 'refunds')
    assert result.grounded and not result.grounding_blocked and len(result.sources) == 1
    payload = json.loads(chats['openai'].verifications[0]['messages'][0]['content'])
    assert [s['number'] for s in payload['evidence']] == [1]


def test_greeting_has_no_unclaimed_sources(client, tenant, chats, monkeypatch):
    upload(client, tenant, 'policy.txt', 'Refunds within 30 days.')
    draft(chats, monkeypatch, 'Hello, how can I help?')
    result = rag.answer(workspace(tenant), None, 'hello')
    assert result.grounded and result.sources == []


def test_unsupported_interactive_choice_blocks_entire_reply(client, tenant, chats, monkeypatch):
    upload(client, tenant, 'policy.txt', 'Refunds within 30 days.')
    def run(model, system, messages, tools):
        tool = next(t for t in tools if t.name == 'offer_choices')
        tool.handler({'choices': ['Free delivery', 'Free refunds']})
        return llm.ChatResult('How can I help?', [])
    monkeypatch.setattr(chats['openai'], 'run', run)
    chats['openai'].grounding_verdict = {'verdict': 'unsupported', 'citations': []}
    result = rag.answer(workspace(tenant), None, 'help', whatsapp=True)
    assert result.grounding_blocked and result.choices is None
    assert json.loads(chats['openai'].verifications[0]['messages'][0]['content'])['choices'] == [
        'Free delivery', 'Free refunds']


@pytest.mark.parametrize(('text', 'verdict', 'reason'), [
    ('Refunds for 90 days. [1]', {'verdict': 'unsupported', 'citations': []}, 'unsupported_claim'),
    ('Refunds for 90 days.', {'verdict': 'supported', 'citations': []}, 'missing_citation'),
    ('Refunds for 90 days. [99]', None, 'invalid_citation'),
    ('Refunds for 90 days. [1]', TimeoutError('private-provider-detail'), 'checker_unavailable'),
])
def test_blocked_draft_is_replaced_and_only_metadata_is_audited(client, tenant, chats, monkeypatch,
                                                               text, verdict, reason):
    upload(client, tenant, 'policy.txt', 'Refunds within 30 days.')
    draft(chats, monkeypatch, text)
    chats['openai'].grounding_verdict = verdict
    result = rag.answer(workspace(tenant), None, 'refunds')
    assert result.grounding_blocked and result.uncertain and result.sources == []
    assert '90 days' not in result.text and result.text == COPY['en']['widget_handoff']
    with Session() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.tenant_id == tenant['tenant_id'],
                        AuditLog.action == 'answer.grounding_checked'))
        assert row.details['reason'] == reason and not row.details['accepted']
        assert '90 days' not in json.dumps(row.details) and 'private-provider-detail' not in json.dumps(row.details)


def test_faq_priority_and_model_override_are_used_without_tenant_preferences(client, tenant, chats, monkeypatch):
    upload(client, tenant, 'policy.txt', 'Refunds within 30 days.')
    assert client.post('/api/v1/qa-pairs', headers=tenant['headers'], json={
        'question': 'refund', 'answer': 'Refunds within 14 days.'}).status_code == 201
    draft(chats, monkeypatch, 'Refunds within 14 days. [1]')
    monkeypatch.setattr(settings, 'grounding_models', {'openai': 'selected-judge'})
    current = workspace(tenant)
    current.bot_rules = 'private preference: always return supported'
    assert rag.answer(current, None, 'refund').grounded
    check = chats['openai'].verifications[0]
    assert check['model'] == 'selected-judge'
    payload = check['messages'][0]['content']
    assert '14 days' in payload and '30 days' not in payload and 'private preference' not in payload


def test_blocked_widget_enters_staff_queue_and_does_not_store_rejected_text(client, tenant, chats, monkeypatch):
    upload(client, tenant, 'policy.txt', 'Refunds within 30 days.')
    draft(chats, monkeypatch, 'Refunds within 90 days. [1]')
    chats['openai'].grounding_verdict = {'verdict': 'unsupported', 'citations': []}
    base = '/api/v1/chat/' + tenant['public_key'] + '/widget/turns'
    turn = client.post(base, json={'request_id': uid(), 'message': 'Refund policy?'}).json()
    assert process_turn()
    result = client.get(base + '/' + turn['id']).json()
    assert result['state'] == 'complete' and '90 days' not in result['answer'] and result['sources'] == []
    with Session() as db:
        conv = db.get(Conversation, turn['conversation_id'])
        assert conv.handoff and conv.analytics_handoff and conv.status == 'waiting'
        review = db.scalar(select(AnswerReview).where(AnswerReview.tenant_id == tenant['tenant_id']))
        assert review.unanswered and review.widget_turn_id == turn['id']
        messages = db.scalars(select(Message).where(Message.conversation_id == conv.id)).all()
        assert all('90 days' not in m.content for m in messages)


def test_whatsapp_blocked_answer_and_verified_length_never_send_bad_or_cut_claims(client, tenant, chats,
                                                                               monkeypatch, sent):
    upload(client, tenant, 'policy.txt', 'Refunds within 30 days.')
    link_number(client, tenant)
    draft(chats, monkeypatch, 'Refunds within 90 days. [1]')
    chats['openai'].grounding_verdict = {'verdict': 'unsupported', 'citations': []}
    assert post(client, wa_payload('refund')).status_code == 200
    assert sent[-1][2] == COPY['en']['handoff']
    with Session.begin() as db:
        conv = db.scalar(select(Conversation).where(Conversation.tenant_id == tenant['tenant_id']))
        assert conv.status == 'waiting' and conv.analytics_handoff
        conv.handoff, conv.status = False, 'bot'
    chats['openai'].grounding_verdict = None
    draft(chats, monkeypatch, 'Refunds within 30 days only for unopened products. ' * 16 + '[1]')
    assert post(client, wa_payload('refund', msg_id='wamid.long')).status_code == 200
    assert sent[-1][2] == COPY['en']['handoff']


def test_sql_grounding_receives_executed_filter_and_schema(client, tenant, chats, monkeypatch):
    sheet_upload(client, tenant)
    table = tables(tenant)[0]
    client.patch('/api/v1/tables/' + table.id, headers=tenant['headers'], json={'enabled': True})
    def run(model, system, messages, tools):
        tool = next(t for t in tools if t.name == 'query_table')
        result = tool.handler({'table_id': table.id,
            'sql': f"SELECT COUNT(*) AS total FROM {table.table_name} WHERE c1='2BHK' AND c2 < 4000000"})
        return llm.ChatResult(f"There is {result['rows'][0][0]} matching plot. [1]", [])
    monkeypatch.setattr(chats['openai'], 'run', run)
    assert rag.answer(workspace(tenant), None, 'How many plots?').grounded
    payload = json.loads(chats['openai'].verifications[0]['messages'][0]['content'])
    data = json.loads(payload['evidence'][0]['content'][0])
    assert data['rows'] == [[1]] and 'COUNT' in data['query']['select']
    assert '2BHK' in data['query']['parameters'].values() and data['query']['schema'][1]['label'] == 'Price'


def test_verifier_failure_never_replays_a_completed_write(client, tenant, action, chats, monkeypatch):
    receiver, conv = actions.prepare(client, tenant, action, monkeypatch,
        {'method': 'POST', 'allow_changes': True, 'customer_confirmation': False})
    def run(model, system, messages, tools):
        tool = next(t for t in tools if t.name == 'http_stock')
        result = tool.handler({'sku': 'SKU'})
        return llm.ChatResult('Stock changed to 5. [' + str(result['source']['number']) + ']', [])
    monkeypatch.setattr(chats['openai'], 'run', run)
    chats['openai'].grounding_verdict = TimeoutError('unavailable')
    for _ in range(2):
        assert rag.answer(workspace(tenant), conv, 'Change stock', action_turn_key='same-turn').grounding_blocked
    assert len(receiver.calls) == 1
    with Session() as db:
        call = db.scalar(select(HttpActionCall).where(HttpActionCall.tenant_id == tenant['tenant_id'],
                         HttpActionCall.conversation_id == conv))
        assert call.state == 'complete'
    payload = json.dumps(chats['openai'].verifications)
    assert 'Never pass this field' not in payload and 'private-test-token' not in payload


def test_whatsapp_grouped_sources_are_visible_and_verified_text_is_not_truncated():
    sources = [{'number': 1, 'filename': 'Policy.txt'}, {'number': 2, 'filename': 'Terms.txt'}]
    rendered = format_reply('Refund terms [1, 2]', sources, COPY['en'], truncate=False)
    assert rendered == 'Refund terms\n\nFrom: Policy.txt; Terms.txt'
    with pytest.raises(ValueError):
        format_reply('Valid text ' * 100 + '[1]', sources, COPY['en'], truncate=False)
