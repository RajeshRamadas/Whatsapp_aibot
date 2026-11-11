"""Resource API tests use real PostgreSQL and Redis, never provider requests."""
import base64

import pytest
from sqlalchemy import select

from app.api.shared import sha
from app.config import settings
from app.db import ApiKey, Contact, Conversation, EvalCase, Lead, Message, QAPair, Session, Source, Tenant, uid
from app.models.evaluations import EvalRun
from app.rate_limits import RateLimiter
from app.services.public_scopes import SCOPES

ROOT = '/api/v1/public'


@pytest.fixture
def api(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b'p' * 32).decode())
    RateLimiter(6000, namespace='public-api').clear()
    monkeypatch.setattr(settings, 'public_api_default_requests_per_minute', 6000)
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Integration', key_hash=sha('full'),
                     key_prefix='full', scopes=sorted(SCOPES)))
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Restricted', key_hash=sha('limited'),
                     key_prefix='limited', scopes=['messages:send']))
        other = Tenant(id=uid(), name='Other', public_key=uid(), admin_key_hash=sha(uid()), plan_id='starter')
        db.add(other)
        db.flush()
        contact = Contact(tenant_id=other.id, channel='web', external_user='foreign')
        conv = Conversation(tenant_id=other.id, channel='web')
        db.add_all([contact, conv])
        db.flush()
        rows = {'contacts': contact, 'conversations': conv,
                'sources': Source(tenant_id=other.id, filename='private.txt', kind='txt', file_path='secret'),
                'qa-pairs': QAPair(tenant_id=other.id, question='private', answer='private', embedding=[1, 0, 0]),
                'leads': Lead(tenant_id=other.id, conversation_id=conv.id),
                'eval-cases': EvalCase(tenant_id=other.id, question='private'),
                'eval-runs': EvalRun(tenant_id=other.id, request_key=uid(), label='Private',
                                    cases=[], context={}, total=0, state='complete'),
                'messages': Message(conversation_id=conv.id, role='user', content='private')}
        db.add_all(rows.values())
        db.flush()
        foreign = {k: v.conversation_id if k == 'leads' else v.id for k, v in rows.items()}
    return {'headers': {'Authorization': 'Bearer full'}, 'foreign': foreign,
            'limited': {'Authorization': 'Bearer limited'}}


def create(client, api, path, body, key=None):
    return client.post(ROOT + '/' + path, json=body,
                       headers={**api['headers'], 'Idempotency-Key': key or uid()})


@pytest.mark.parametrize('resource', ['sources', 'qa-pairs', 'contacts', 'conversations',
                                     'leads', 'eval-cases', 'eval-runs', 'messages'])
def test_read_boundary(client, tenant, api, resource):
    path = ROOT + '/' + resource
    assert client.get(path).status_code == 401
    assert client.get(path, headers=api['limited']).status_code == 403
    assert client.get(path, headers=api['headers']).json()['items'] == []
    assert client.get(path, headers=api['headers'], params={'limit': 101}).status_code == 422
    assert client.get(path, headers=api['headers'], params={'cursor': 'bad'}).status_code == 422
    foreign = path + '/' + str(api['foreign'][resource])
    assert client.get(foreign).status_code == 401
    assert client.get(foreign, headers=api['limited']).status_code == 403
    assert client.get(foreign, headers=api['headers']).status_code == 404
    assert client.get(path + '/invalid', headers=api['headers']).status_code == 422


def test_resource_round_trip_and_replay(client, api):
    body = {'channel': 'web', 'external_user': 'customer', 'name': 'Asha'}
    first = create(client, api, 'contacts', body, 'contact')
    assert first.status_code == 201, first.text
    contact = first.json()
    assert contact['opt_in_at'] is None
    assert create(client, api, 'contacts', body, 'contact').json() == contact
    assert create(client, api, 'contacts', body, 'another').status_code == 409
    assert create(client, api, 'conversations', {'contact_id': api['foreign']['contacts']}).status_code == 404
    conv = create(client, api, 'conversations', {'contact_id': contact['id']}).json()
    lead = create(client, api, 'leads', {'conversation_id': conv['id'], 'fields': {'need': 'A demo'}})
    assert lead.status_code == 201, lead.text
    case = create(client, api, 'eval-cases', {'question': 'Refund?', 'expected_keywords': ['refund']})
    assert case.status_code == 201, case.text
    for resource, identity in [('contacts', contact['id']), ('conversations', conv['id']),
                               ('leads', conv['id']), ('eval-cases', case.json()['id'])]:
        assert client.get(ROOT + '/' + resource + '/' + identity, headers=api['headers']).status_code == 200
    listing = client.get(ROOT + '/contacts', headers=api['headers']).json()
    assert listing['items'] == [contact] and listing['next_cursor'] is None


def test_pagination_survives_deleted_position(client, api):
    for i in range(3):
        create(client, api, 'contacts', {'channel': 'web', 'external_user': str(i)})
    first = client.get(ROOT + '/contacts?limit=1', headers=api['headers']).json()
    with Session.begin() as db:
        db.delete(db.scalar(select(Contact).where(Contact.id == first['items'][0]['id'])))
    more = client.get(ROOT + '/contacts', headers=api['headers'], params={'cursor': first['next_cursor']})
    assert more.status_code == 200 and len(more.json()['items']) == 2
    assert client.get(ROOT + '/sources', headers=api['headers'],
                      params={'cursor': first['next_cursor']}).status_code == 422


@pytest.mark.parametrize(('path', 'body'), [
    ('contacts', {'channel': 'web', 'external_user': 'valid'}),
    ('conversations', {'contact_id': '00000000-0000-0000-0000-000000000001'}),
    ('leads', {'conversation_id': '00000000-0000-0000-0000-000000000001', 'fields': {'name': 'Asha'}}),
    ('eval-cases', {'question': 'Refund?', 'expected_keywords': ['refund']})])
def test_write_boundary(client, api, path, body):
    assert client.post(ROOT + '/' + path, json=body, headers={'Idempotency-Key': uid()}).status_code == 401
    assert client.post(ROOT + '/' + path, json=body,
                       headers={**api['limited'], 'Idempotency-Key': uid()}).status_code == 403
    assert client.post(ROOT + '/' + path, json=body, headers=api['headers']).status_code == 422
    assert create(client, api, path, {}).status_code == 422
    if path in ('leads', 'conversations'):
        assert create(client, api, path, body).status_code == 404


def test_case_mutations_and_message_filter(client, api):
    body = {'question': 'No answer?', 'expect_no_answer': True}
    identity = create(client, api, 'eval-cases', body).json()['id']
    path = ROOT + '/eval-cases/' + identity
    assert client.put(path, json=body, headers=api['headers']).status_code == 200
    assert client.put(path, json={}, headers=api['headers']).status_code == 422
    for method in ('put', 'delete'):
        kwargs = {'json': body} if method == 'put' else {}
        call = getattr(client, method)
        assert call(path, **kwargs).status_code == 401
        assert call(path, headers=api['limited'], **kwargs).status_code == 403
        assert call(ROOT + '/eval-cases/' + api['foreign']['eval-cases'],
                    headers=api['headers'], **kwargs).status_code == 404
        assert call(ROOT + '/eval-cases/invalid', headers=api['headers'], **kwargs).status_code == 422
    assert client.delete(path, headers=api['headers']).status_code == 204
    assert client.get(ROOT + '/messages', headers=api['headers'],
                      params={'conversation_id': api['foreign']['conversations']}).status_code == 404


@pytest.mark.parametrize(('path', 'body'), [('contacts', {'name': 'Updated'}),
    ('conversations', {'status': 'closed', 'labels': ['resolved']}),
    ('leads', {'status': 'qualified', 'notes': 'Ready'})])
def test_patch_boundaries(client, tenant, api, path, body):
    with Session.begin() as db:
        contact = Contact(tenant_id=tenant['tenant_id'], channel='web', external_user='patch')
        db.add(contact)
        db.flush()
        conv = Conversation(tenant_id=tenant['tenant_id'], contact_id=contact.id)
        db.add(conv)
        db.flush()
        db.add(Lead(tenant_id=tenant['tenant_id'], conversation_id=conv.id))
        identity = contact.id if path == 'contacts' else conv.id
    url = ROOT + '/' + path + '/' + identity
    assert client.patch(url, json=body).status_code == 401
    assert client.patch(url, json=body, headers=api['limited']).status_code == 403
    assert client.patch(url, json=body, headers=api['headers']).status_code == 200
    assert client.patch(ROOT + '/' + path + '/' + api['foreign'][path],
                        json=body, headers=api['headers']).status_code == 404
    assert client.patch(ROOT + '/' + path + '/invalid', json=body, headers=api['headers']).status_code == 422
    assert client.patch(url, json={'unexpected': True}, headers=api['headers']).status_code == 422
