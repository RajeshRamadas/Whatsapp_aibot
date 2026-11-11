"""Text/template sends and eval runs commit once and execute through existing workers."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import whatsapp
from app.api.shared import sha
from app.db import ApiKey, Channel, Contact, Conversation, Message, Session, Template, Tenant, now, uid
from app.jobs.worker import dispatch_agent_reply
from app.models.evaluations import EvalRun
from app.services import evaluation_runs
from tests import test_public_resources as fixtures
from tests.test_public_resources import ROOT, create

api = fixtures.api


@pytest.fixture
def conversations(tenant, api):
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Wrong scope', key_hash=sha('wrong'),
                     key_prefix='wrong', scopes=['knowledge:read']))
        channel = Channel(tenant_id=tenant['tenant_id'], kind='whatsapp', external_id='public-phone')
        contact = Contact(tenant_id=tenant['tenant_id'], channel='whatsapp', external_user='15550001234')
        db.add_all([channel, contact])
        db.get(Tenant, tenant['tenant_id']).whatsapp_phone_number_id = 'public-phone'
        db.flush()
        template = Template(channel_id=channel.id, meta_id='public-template', name='follow_up', language='en_US',
                            category='UTILITY', status='APPROVED', body='Hello {{1}}')
        conv = Conversation(tenant_id=tenant['tenant_id'], channel='whatsapp', contact_id=contact.id,
                            external_user=contact.external_user, last_inbound_at=now())
        web = Conversation(tenant_id=tenant['tenant_id'], channel='web')
        db.add_all([template, conv, web])
        db.flush()
        return {'wa': conv.id, 'web': web.id, 'template': template.id, 'contact': contact.id}


def test_send_text_replay_and_worker(client, api, conversations, sent):
    for channel in ('web', 'wa'):
        body = {'type': 'text', 'conversation_id': conversations[channel], 'text': 'Hello'}
        first = create(client, api, 'messages', body, channel)
        assert first.status_code == 201, first.text
        assert first.json()['delivery_status'] == ('sent' if channel == 'web' else 'queued')
        assert create(client, api, 'messages', body, channel).json() == first.json()
    assert sent == []
    assert dispatch_agent_reply() and len(sent) == 1
    assert not dispatch_agent_reply()
    page = client.get(ROOT + '/messages', headers=api['headers'],
                      params={'conversation_id': conversations['wa'], 'limit': 1}).json()
    assert len(page['items']) == 1 and page['items'][0]['content'] == 'Hello'
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 3  # Includes foreign fixture.


def test_template_eligibility_and_dispatch(client, api, conversations, monkeypatch):
    body = {'type': 'template', 'conversation_id': conversations['wa'],
            'template_id': conversations['template'], 'values': ['Asha']}
    assert create(client, api, 'messages', body).status_code == 409
    with Session.begin() as db:
        db.get(Conversation, conversations['wa']).last_inbound_at = now() - timedelta(days=2)
    assert create(client, api, 'messages', body).json()['error']['code'] == 'consent_required'
    assert create(client, api, 'messages', {'type': 'text', 'conversation_id': conversations['wa'],
                                          'text': 'Hello'}).status_code == 409
    with Session.begin() as db:
        db.get(Contact, conversations['contact']).opt_in_at = now()
    assert create(client, api, 'messages', {**body, 'template_id': uid()}).status_code == 404
    assert create(client, api, 'messages', {**body, 'values': []}).status_code == 422
    first = create(client, api, 'messages', body, 'template')
    assert first.status_code == 201 and first.json()['content'] == 'Hello Asha'
    assert create(client, api, 'messages', body, 'template').json() == first.json()
    calls = []
    monkeypatch.setattr(whatsapp, 'graph_message',
                        lambda *a, **kw: calls.append(kw or a) or {'messages': [{'id': 'sent'}]})
    assert dispatch_agent_reply() and len(calls) == 1
    assert not dispatch_agent_reply()


def test_actions_boundary(client, api, conversations):
    requests = [('messages', {'type': 'text', 'conversation_id': conversations['web'], 'text': 'Hi'}),
                ('eval-runs', {'confirm_cost': True}), ('eval-runs/' + uid() + '/cancel', {'confirm': True})]
    for path, body in requests:
        assert client.post(ROOT + '/' + path, json=body, headers={'Idempotency-Key': uid()}).status_code == 401
        assert client.post(ROOT + '/' + path, json=body,
                           headers={'Authorization': 'Bearer wrong', 'Idempotency-Key': uid()}).status_code == 403
        assert create(client, api, path, {}).status_code == 422
        assert client.post(ROOT + '/' + path, json=body, headers=api['headers']).status_code == 422
    assert create(client, api, 'messages', {'type': 'text', 'text': 'Hi',
                  'conversation_id': api['foreign']['conversations']}).status_code == 404
    assert create(client, api, 'eval-runs', {'confirm_cost': True,
                  'case_ids': [api['foreign']['eval-cases']]}).status_code == 404
    assert create(client, api, 'eval-runs/' + api['foreign']['eval-runs'] + '/cancel',
                  {'confirm': True}).status_code == 404
    assert client.get(ROOT + '/templates').status_code == 401
    assert client.get(ROOT + '/templates', headers={'Authorization': 'Bearer wrong'}).status_code == 403
    assert client.get(ROOT + '/templates', headers=api['headers'], params={'limit': 101}).status_code == 422
    listing = client.get(ROOT + '/templates', headers=api['headers']).json()
    assert [t['id'] for t in listing['items']] == [conversations['template']]


def test_eval_run_atomic_replay_and_cancel(client, api):
    case = create(client, api, 'eval-cases', {'question': 'Refund?', 'expected_keywords': ['refund']}).json()
    body = {'confirm_cost': True, 'case_ids': [case['id']]}
    first = create(client, api, 'eval-runs', body, 'run')
    assert first.status_code == 202, first.text
    assert create(client, api, 'eval-runs', body, 'run').json() == first.json()
    assert create(client, api, 'eval-runs', body, 'other').status_code == 409
    assert evaluation_runs.process_one()
    path = 'eval-runs/' + first.json()['id']
    report = client.get(ROOT + '/' + path, headers=api['headers'])
    assert report.json()['state'] == 'complete'
    assert 'bot' not in report.json()['context'] and 'request' not in report.json()['context']
    second = create(client, api, 'eval-runs', body, 'second').json()
    cancelled = create(client, api, 'eval-runs/' + second['id'] + '/cancel', {'confirm': True}, 'cancel')
    assert cancelled.status_code == 200 and cancelled.json()['state'] == 'cancelled'
    again = create(client, api, 'eval-runs/' + second['id'] + '/cancel', {'confirm': True}, 'cancel')
    assert again.json() == cancelled.json()
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(EvalRun)) == 3
