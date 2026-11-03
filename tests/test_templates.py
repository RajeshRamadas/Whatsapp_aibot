from datetime import timedelta

import httpx

from app import whatsapp
from app.api import templates
from app.db import Contact, Conversation, Message, Session, now
from app.jobs.worker import dispatch_agent_reply
from tests.test_whatsapp import link_number, post

BASE = '/api/v1/whatsapp/templates'
BODY = {'name': 'follow_up', 'language': 'en_US', 'category': 'UTILITY',
        'body': 'Hello {{1}}, your appointment is ready.', 'examples': ['Sam']}


class FakeMeta:
    def verify_phone(self, waba, phone, token):
        assert (waba, phone) == ('123', 'PNID1')

    def request(self, path, *, token, payload):
        assert path == '123/message_templates'
        assert payload['components'][0]['example'] == {'body_text': [['Sam']]}
        return {'id': 'meta-1', 'status': 'PENDING'}


def status_event(status):
    return {'entry': [{'id': '123', 'changes': [{'field': 'message_template_status_update',
                                                 'value': {'message_template_id': 'meta-1', 'event': status}}]}]}


def test_template_submission_approval_and_send(client, tenant, monkeypatch):
    link_number(client, tenant)
    headers = tenant['headers']
    monkeypatch.setattr(templates, 'MetaSignup', FakeMeta)
    assert client.put(BASE + '/account', headers=headers, json={'waba_id': '123'}).status_code == 200
    assert client.post(BASE, headers=headers, json={**BODY, 'examples': []}).status_code == 422
    result = client.post(BASE, headers=headers, json=BODY)
    assert result.status_code == 201 and result.json()['status'] == 'PENDING'
    assert client.get(BASE + '?approved=true', headers=headers).json() == []
    assert post(client, status_event('APPROVED')).status_code == 200
    [row] = client.get(BASE + '?approved=true', headers=headers).json()
    conversation = whatsapp.conversation_for(tenant['tenant_id'], '15550001234')
    with Session.begin() as db:
        db.get(Conversation, conversation.id).last_inbound_at = now() - timedelta(days=2)
    path = f'/api/v1/inbox/conversations/{conversation.id}/template'
    payload = {'template_id': row['id'], 'values': ['Sam']}
    send_headers = {**headers, 'Idempotency-Key': 'template-first'}
    assert client.post(path, headers=send_headers, json=payload).status_code == 409
    with Session.begin() as db:
        db.get(Contact, conversation.contact_id).opt_in_at = now()
    first = client.post(path, headers=send_headers, json=payload)
    assert first.status_code == 200 and first.json()['status'] == 'queued'
    assert client.post(path, headers=send_headers, json=payload).json() == first.json()

    sent = []

    def graph(phone, payload, tenant_id):
        sent.append((phone, payload, tenant_id))
        return httpx.Response(200, json={'messages': [{'id': 'wamid.template'}]})

    monkeypatch.setattr(whatsapp, 'graph_message', graph)
    assert dispatch_agent_reply()
    assert sent == [('PNID1', {'recipient_type': 'individual', 'to': '15550001234', 'type': 'template',
                              'template': {'name': 'follow_up', 'language': {'code': 'en_US'},
                                           'components': [{'type': 'body', 'parameters': [
                                               {'type': 'text', 'text': 'Sam'}]}]}}, tenant['tenant_id'])]
    with Session() as db:
        message = db.get(Message, first.json()['message_id'])
        assert message.delivery_status == 'sent' and message.content == 'Hello Sam, your appointment is ready.'

    queued = client.post(path, headers={**headers, 'Idempotency-Key': 'template-second'}, json=payload)
    assert queued.status_code == 200
    assert post(client, status_event('PAUSED')).status_code == 200
    assert dispatch_agent_reply()
    assert len(sent) == 1
    with Session() as db:
        assert db.get(Message, queued.json()['message_id']).delivery_status == 'failed'


def test_sync_and_tenant_isolation(client, tenant, monkeypatch):
    link_number(client, tenant)
    headers = tenant['headers']
    monkeypatch.setattr(templates, 'MetaSignup', FakeMeta)
    assert client.put(BASE + '/account', headers=headers, json={'waba_id': '123'}).status_code == 200
    data = {'id': 'meta-2', 'name': 'synced', 'language': 'en_US', 'category': 'UTILITY', 'status': 'APPROVED',
            'components': [{'type': 'BODY', 'text': 'Ready'}]}
    monkeypatch.setattr(templates, 'list_meta', lambda waba, token: [data])
    assert client.post(BASE + '/sync', headers=headers).json() == {'synced': 1}
    assert client.get(BASE + '?approved=true', headers=headers).json()[0]['name'] == 'synced'
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other templates'}).json()
    foreign = {'X-Admin-Key': other['admin_key']}
    assert client.get(BASE, headers=foreign).json() == []
    assert client.get(BASE).status_code == 401
    monkeypatch.setattr(templates, 'list_meta', lambda waba, token: [])
    assert client.post(BASE + '/sync', headers=headers).json() == {'synced': 0}
    assert client.get(BASE + '?approved=true', headers=headers).json() == []
