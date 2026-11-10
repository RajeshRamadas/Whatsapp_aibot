"""Language selection and editable notice contracts, with offline provider fakes."""
import hashlib
from string import Formatter

import pytest
from sqlalchemy import select

from app import rag
from app.db import ApiKey, AuditLog, Session, Tenant
from app.services.customer_languages import COPY, LANGUAGES, copy_for, locale_for, validate_messages
from app.services.widget_turns import process_turn
from tests.conftest import wa_payload
from tests.test_whatsapp import link_number, post

BASE = '/api/v1/bot/messages'


@pytest.mark.parametrize('question,code', [
    ('What are your hours?', 'en'), ('आप कब खुले हैं?', 'hi'), ('aap kab khulte hain?', 'hi-Latn'),
    ('ನಿಮ್ಮ ಕೆಲಸದ ಸಮಯ ಯಾವುದು?', 'kn'), ('உங்கள் வேலை நேரம் என்ன?', 'ta'),
    ('మీ పని వేళలు ఏమిటి?', 'te'), ('तुमची वेळ काय आहे?', 'mr'), ('আপনার কাজের সময় কী?', 'bn'),
])
def test_language_contract_switches_each_turn(client, tenant, chats, question, code):
    with Session() as db:
        workspace = db.get(Tenant, tenant['tenant_id'])
    workspace.bot_rules = 'Always answer in English.'
    assert locale_for(question) == code
    result = rag.answer(workspace, None, question + ' SKU-42 ₹500')
    prompt = chats['openai'].calls[-1]['system']
    assert f'Notice-language hint: {code}' in prompt
    assert prompt.index('LANGUAGE AND SCRIPT') < prompt.index('TENANT PREFERENCES')
    assert 'Hinglish in Latin' in prompt and 'numbered citations exactly' in prompt
    assert chats['openai'].calls[-1]['messages'][-1]['content'].endswith('SKU-42 ₹500')
    assert result.search_query.endswith('SKU-42 ₹500')


def test_all_catalogs_have_safe_placeholders_and_ambiguous_text_uses_default():
    assert set(COPY) == set(LANGUAGES)
    for code, messages in COPY.items():
        assert set(messages) == set(COPY['en'])
        assert validate_messages(messages) == messages
        for key, value in messages.items():
            fields = {field for _, field, _, _ in Formatter().parse(value) if field is not None}
            expected = {'bot', 'business'} if key == 'greeting' else {'opening'} if key == 'closed' else set()
            assert fields == expected, (code, key)
    assert locale_for('1234 …', 'ta-IN') == 'ta'
    assert locale_for('।', 'kn') == 'kn'
    assert locale_for('कधी?', 'hi') == 'mr'
    assert locale_for('क्या?', 'mr') == 'hi'


def test_notice_edits_are_admin_scoped_validated_and_resettable(client, tenant):
    headers = tenant['headers']
    body = {'language': 'hi', 'messages': {'greeting': 'नमस्ते {bot}, {business} में स्वागत है।'}}
    assert client.get(BASE).status_code == client.put(BASE, json=body).status_code == 401
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Reader', key_prefix='fixture', scopes=['viewer'],
                     key_hash=hashlib.sha256(b'language-reader').hexdigest()))
    viewer = {'Authorization': 'Bearer language-reader'}
    assert client.get(BASE, headers=viewer).status_code == 403
    assert client.put(BASE, headers=viewer, json=body).status_code == 403
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other language workspace'}).json()
    foreign_context = {**headers, 'X-Workspace-Id': other['tenant_id']}
    assert client.get(BASE, headers=foreign_context).status_code == 404
    assert client.put(BASE, headers=foreign_context, json=body).status_code == 404
    for bad in [{**body, 'language': 'xx'}, {**body, 'tenant_id': other['tenant_id']},
                {**body, 'messages': {'not_a_message': 'value'}}, {**body, 'messages': {'greeting': 'Missing names'}},
                {**body, 'messages': {'greeting': '{bot.__class__} {business}'}},
                {**body, 'messages': {'greeting': '{bot:1000000000} {business}'}},
                {**body, 'messages': {'greeting': '{bot!r} {business}'}},
                {**body, 'messages': {'options': 'x' * 21}}]:
        assert client.put(BASE, headers=headers, json=bad).status_code == 422
    assert client.put(BASE, headers=headers, json=body).status_code == 200
    assert client.get(BASE, headers={'X-Admin-Key': other['admin_key']}).json()['overrides'] == {}
    with Session() as db:
        workspace = db.get(Tenant, tenant['tenant_id'])
        assert copy_for(workspace, 'नमस्ते')['greeting'] == body['messages']['greeting']
        assert copy_for(workspace, 'What are your hours?')['greeting'] == COPY['en']['greeting']
        event = db.scalar(select(AuditLog).where(AuditLog.action == 'bot.messages.updated'))
        assert event.details == {'language': 'hi'}
    assert client.put(BASE, headers=headers, json={'language': 'hi', 'messages': {}}).json()['overrides']['hi'] == {}
    assert COPY['hi']['greeting'] != body['messages']['greeting']


def test_notices_follow_customer_language_across_whatsapp_widget_and_legacy_chat(client, tenant, sent):
    link_number(client, tenant)
    assert post(client, wa_payload('நிறுவன நேரம் என்ன?', msg_id='wamid.tamil')).status_code == 200
    assert sent and COPY['ta']['greeting'].split('{bot}')[0] in sent[-1][2]
    base = '/api/v1/chat/' + tenant['public_key']
    body = {'request_id': '29117ce6-636d-4d2e-a768-d51e5604275a', 'message': 'ನಮಸ್ಕಾರ', 'handoff': True}
    assert client.post(base + '/widget/turns', json=body).status_code == 202
    assert process_turn()
    response = client.get(base + '/widget/turns/' + body['request_id']).json()
    assert response['answer'] == COPY['kn']['widget_handoff']
    legacy = client.post(base, json={'message': 'Hello'}).json()
    with Session.begin() as db:
        from app.db import Conversation
        db.get(Conversation, legacy['conversation_id']).handoff = True
    response = client.post(base, json={'conversation_id': legacy['conversation_id'], 'message': 'বাংলা ভাষা'})
    assert response.json()['answer'] == COPY['bn']['web_handoff']
