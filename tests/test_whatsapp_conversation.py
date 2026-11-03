from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app import whatsapp
from app.db import Contact, Session, Tenant
from app.services.whatsapp_copy import COPY, format_reply, handoff_notice
from app.whatsapp import mark_typing
from tests.conftest import wa_payload
from tests.test_whatsapp import link_number, post


def test_whatsapp_formatting_keeps_facts_and_citations():
    sources = [{'number': 1, 'filename': 'Fees.pdf', 'page': 2}, {'number': 2, 'filename': 'Other', 'page': None}]
    text = format_reply('## Fees\n- **₹500** [1]\n[Details](https://example.com/fees)', sources, COPY['en'])
    assert text == '*Fees*\n• *₹500*\nDetails (https://example.com/fees)\n\nFrom: Fees.pdf, page 2'
    long = format_reply('A long sentence. ' * 100 + '[1]', sources, COPY['en'])
    assert len(long) <= 600
    assert 'Want the full details?' in long and long.endswith('From: Fees.pdf, page 2')


def test_localized_closed_handoff_and_marketing_consent(client, tenant, sent):
    link_number(client, tenant)
    headers = tenant['headers']
    response = client.patch('/api/v1/tenant', headers=headers, json={
        'default_language': 'hi', 'whatsapp_opt_out_keywords': ['रोकें'],
        'business_hours': '{"0":["09:00","17:00"]}', 'timezone': 'Asia/Kolkata',
    })
    assert response.status_code == 200
    with Session() as db:
        row = db.get(Tenant, tenant['tenant_id'])
        notice = handoff_notice(row, datetime(2026, 11, 1, 16, tzinfo=timezone.utc))
    assert '2026-11-02 09:00' in notice and 'अभी हम बंद हैं' in notice
    assert post(client, wa_payload('रोकें')).status_code == 200
    assert 'मार्केटिंग संदेश बंद' in sent[-1][2]
    with Session() as db:
        assert db.scalar(select(Contact)).opt_out_at is not None
    assert post(client, wa_payload('START', msg_id='wamid.start')).status_code == 200
    with Session() as db:
        contact = db.scalar(select(Contact))
        assert contact.opt_out_at is None and contact.opt_in_at is not None


def test_typing_and_list_payloads(client, tenant, chats, monkeypatch):
    link_number(client, tenant)
    requests = []

    def graph(url, **kwargs):
        requests.append(kwargs['json'])
        return httpx.Response(200, request=httpx.Request('POST', url), json={'messages': [{'id': 'wamid.sent'}]})

    monkeypatch.setattr(whatsapp.httpx, 'post', graph)
    monkeypatch.setattr(whatsapp, 'mark_typing', mark_typing)
    chats['openai'].tool_turns = [
        lambda results, enabled: ('', [('offer_choices', {'choices': ['A', 'B', 'C', 'D']})]),
        lambda results, enabled: ('Pick a service.', []),
    ]
    assert post(client, wa_payload('What services?')).status_code == 200
    assert requests[0]['status'] == 'read' and requests[0]['typing_indicator'] == {'type': 'text'}
    assert requests[0]['message_id'] == 'wamid.1'
    assert requests[1]['type'] == 'interactive'
    interactive = requests[1]['interactive']
    assert interactive['type'] == 'list'
    assert [row['title'] for row in interactive['action']['sections'][0]['rows']] == ['A', 'B', 'C', 'D']


def test_uncertain_reply_offers_handoff_buttons(client, tenant, chats, monkeypatch):
    link_number(client, tenant)
    replies = []
    monkeypatch.setattr(whatsapp, 'send_text', lambda *args, **kwargs: replies.append(kwargs['interactive']))
    chats['openai'].tool_turns = [
        lambda results, enabled: ('', [('offer_handoff', {})]),
        lambda results, enabled: ('', []),
    ]
    assert post(client, wa_payload('Unknown question')).status_code == 200
    buttons = replies[0]['action']['buttons']
    assert [button['reply']['id'] for button in buttons] == ['handoff', 'handoff-decline']
