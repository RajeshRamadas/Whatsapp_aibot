import csv
import hashlib
import io
import json

import pytest
from sqlalchemy import select

from app import whatsapp
from app.config import settings
from app.db import ApiKey, AuditLog, Contact, Message, Session, Template, now, uid
from app.jobs.worker import dispatch_agent_reply
from app.services.channels import whatsapp_channel
from tests.conftest import wa_payload
from tests.test_whatsapp import link_number, post

BASE = '/api/v1'
PAST = '2026-01-01T00:00:00Z'


def upload(client, headers, rows, columns=None):
    columns = columns or ['phone', 'name', 'language', 'attributes', 'opt_in_at', 'opt_in_source']
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value) if isinstance(value, dict) else value for key, value in row.items()})
    return client.post(BASE + '/contacts/import', headers=headers,
                       files={'file': ('contacts.csv', buffer.getvalue().encode(), 'text/csv')})


def test_csv_import_is_normalized_repeatable_and_private(client, tenant, caplog):
    rows = [{'phone': '+15550000001', 'name': 'Private Contact', 'language': 'en',
             'attributes': {'city': 'Pune'}, 'opt_in_at': PAST, 'opt_in_source': 'website-form'},
            {'phone': '15550000002', 'name': 'No consent'}]
    with caplog.at_level('INFO'):
        imported = upload(client, tenant['headers'], rows)
    assert imported.status_code == 200, imported.text
    assert imported.json() == {'processed': 2, 'created': 2, 'updated': 0, 'consents_recorded': 1}
    assert '15550000001' not in caplog.text and 'Private Contact' not in caplog.text
    result = client.get(BASE + '/contacts?opted_in=true', headers=tenant['headers']).json()
    assert result['total'] == 1 and result['items'][0]['external_user'] == '15550000001'
    assert result['items'][0]['consent_source'] == 'website-form'
    assert upload(client, tenant['headers'], rows).json()['created'] == 0
    assert upload(client, tenant['headers'], rows).json()['consents_recorded'] == 0
    assert client.get(BASE + '/contacts?search=%25', headers=tenant['headers']).json()['total'] == 0
    assert client.get(BASE + '/contacts?limit=1&offset=1', headers=tenant['headers']).json()['total'] == 2
    with Session() as db:
        assert len(db.scalars(select(Contact)).all()) == 2
        consent = db.scalars(select(AuditLog).where(AuditLog.action == 'contact.opted_in')).all()
        assert len(consent) == 1 and consent[0].details['origin'] == 'csv'
        assert '15550000001' not in str(consent[0].details)


def test_stop_survives_csv_reimport_and_blocks_an_already_queued_template(client, tenant, sent, monkeypatch):
    headers = tenant['headers']
    link_number(client, tenant)
    rows = [{'phone': '+15551234567', 'opt_in_at': PAST, 'opt_in_source': 'signed-form'}]
    assert upload(client, headers, rows).status_code == 200
    conv = whatsapp.conversation_for(tenant['tenant_id'], '15551234567')
    with Session.begin() as db:
        channel = whatsapp_channel(db, tenant['tenant_id'])
        template = Template(id=uid(), channel_id=channel.id, meta_id='fake', name='follow_up', language='en',
                            category='MARKETING', status='APPROVED', body='Hello')
        db.add(template)
        db.flush()
        template_id = template.id
    path = BASE + f'/inbox/conversations/{conv.id}/template'
    queued = client.post(path, headers={**headers, 'Idempotency-Key': 'consent-regression'},
                         json={'template_id': template_id, 'values': []})
    assert queued.status_code == 200, queued.text
    assert post(client, wa_payload(' STOP ')).status_code == 200
    assert 'Marketing messages are stopped' in sent[-1][2]
    rows[0]['opt_in_at'] = now().isoformat()
    assert upload(client, headers, rows).json()['consents_recorded'] == 0
    assert client.get(BASE + '/contacts?opted_in=true', headers=headers).json()['total'] == 0
    monkeypatch.setattr(whatsapp, 'graph_message', lambda *a, **kw: pytest.fail('Opted-out recipient sent to Meta'))
    assert dispatch_agent_reply()
    with Session() as db:
        assert db.get(Message, queued.json()['message_id']).delivery_status == 'failed'
    contact_id = client.get(BASE + '/contacts', headers=headers).json()['items'][0]['id']
    accepted = client.put(BASE + f'/contacts/{contact_id}/consent', headers=headers,
                          json={'opted_in': True, 'source': 'new-signed-consent'})
    assert accepted.status_code == 200 and accepted.json()['marketing_eligible']
    assert accepted.json()['consent_source'] == 'new-signed-consent'
    assert accepted.json()['opt_out_at'] is not None  # Operator updates retain the prior opt-out time.


def test_segments_are_dynamic_and_have_crud_validation_and_pagination(client, tenant):
    headers = tenant['headers']
    upload(client, headers, [{'phone': '15550000001', 'language': 'en', 'attributes': {'city': 'Pune'},
                             'opt_in_at': PAST, 'opt_in_source': 'form'}, {'phone': '15550000002'}])
    body = {'name': 'Pune opt-ins', 'filters': {'language': 'en', 'attributes': {'city': 'Pune'}}}
    created = client.post(BASE + '/segments', headers=headers, json=body)
    assert created.status_code == 201, created.text
    identity = created.json()['id']
    assert client.post(BASE + '/segments', headers=headers, json=body).status_code == 409
    matched = client.get(BASE + '/contacts', params={'segment_id': identity}, headers=headers).json()
    assert matched['total'] == 1
    contact_id = matched['items'][0]['id']
    assert client.put(BASE + f'/contacts/{contact_id}/consent', headers=headers,
                      json={'opted_in': False, 'source': 'customer-request'}).status_code == 200
    assert client.get(BASE + '/contacts', params={'segment_id': identity}, headers=headers).json()['total'] == 0
    body['filters']['eligible_only'] = False
    assert client.put(BASE + f'/segments/{identity}', headers=headers, json=body).status_code == 200
    assert client.get(BASE + '/contacts', params={'segment_id': identity}, headers=headers).json()['total'] == 1
    assert client.get(BASE + '/segments?limit=1', headers=headers).json()['total'] == 1
    assert client.get(BASE + '/segments?offset=1', headers=headers).json()['items'] == []
    with Session() as db:
        events = db.scalars(select(AuditLog).where(AuditLog.action.in_(['segment.created', 'segment.updated']))).all()
        assert events and all(event.actor_type == 'api_key' for event in events)
    assert client.delete(BASE + f'/segments/{identity}', headers=headers).status_code == 204
    assert client.get(BASE + '/contacts', params={'segment_id': identity}, headers=headers).status_code == 404


@pytest.mark.parametrize('method,path,kwargs', [
    ('get', '/contacts', {}), ('get', '/segments', {}),
    ('post', '/contacts/import', {'files': {'file': ('x.csv', b'phone\n15550000001')}}),
    ('put', '/contacts/00000000-0000-0000-0000-000000000000/consent', {'json': {'opted_in': True, 'source': 'form'}}),
    ('post', '/segments', {'json': {'name': 'Test', 'filters': {}}}),
    ('put', '/segments/00000000-0000-0000-0000-000000000000', {'json': {'name': 'Test', 'filters': {}}}),
    ('delete', '/segments/00000000-0000-0000-0000-000000000000', {}),
])
def test_every_contact_endpoint_requires_credentials_and_role(client, tenant, method, path, kwargs):
    assert client.request(method, BASE + path, **kwargs).status_code == 401
    role = 'viewer' if method == 'get' else 'agent'
    secret = 'test-role-key'
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Role test', key_prefix=secret,
                      key_hash=hashlib.sha256(secret.encode()).hexdigest(), scopes=[role]))
    denied = client.request(method, BASE + path, headers={'Authorization': 'Bearer ' + secret}, **kwargs)
    assert denied.status_code == 403
    if method == 'get':
        with Session.begin() as db:
            key = db.scalar(select(ApiKey).where(ApiKey.tenant_id == tenant['tenant_id'], ApiKey.name == 'Role test'))
            key.scopes = ['agent']
        assert client.request(method, BASE + path, headers={'Authorization': 'Bearer ' + secret}).status_code == 200


def test_contacts_and_segments_are_tenant_isolated(client, tenant):
    headers = tenant['headers']
    upload(client, headers, [{'phone': '15550000001'}])
    contact_id = client.get(BASE + '/contacts', headers=headers).json()['items'][0]['id']
    body = {'name': 'Private audience', 'filters': {}}
    identity = client.post(BASE + '/segments', headers=headers, json=body).json()['id']
    other = client.post(BASE + '/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    foreign = {'X-Admin-Key': other['admin_key']}
    assert client.get(BASE + '/contacts', headers=foreign).json()['total'] == 0
    assert client.get(BASE + '/segments', headers=foreign).json()['total'] == 0
    assert client.get(BASE + '/contacts', params={'segment_id': identity}, headers=foreign).status_code == 404
    assert client.put(BASE + f'/contacts/{contact_id}/consent', headers=foreign,
                      json={'opted_in': True, 'source': 'forged'}).status_code == 404
    assert client.put(BASE + f'/segments/{identity}', headers=foreign, json=body).status_code == 404
    assert client.delete(BASE + f'/segments/{identity}', headers=foreign).status_code == 404
    assert upload(client, foreign, [{'phone': '15550000001'}]).json()['created'] == 1
    assert client.get(BASE + '/contacts', headers=foreign).json()['items'][0]['id'] != contact_id
    assert upload(client, {**headers, 'X-Workspace-Id': other['tenant_id']},
                  [{'phone': '15550000002'}]).status_code == 404


@pytest.mark.parametrize('content', [b'', b'phone\n', b'phone,phone\n15550000001,15550000002',
    b'phone,tenant_id\n15550000001,forged', b'phone\n15550000001\n+15550000001', b'phone\nlocal',
    b'phone,name\n15550000001', b'phone\n15550000001,extra', b'\xff',
    b'phone,opt_in_at\n15550000001,2026-01-01T00:00:00Z',
    b'phone,attributes\n15550000001,[]', b'phone,attributes\n15550000001,invalid-json',
    b'phone,name\n15550000001,"unterminated',
    b'phone,opt_in_at,opt_in_source\n15550000001,2026-01-01,form',
    b'phone,opt_in_at,opt_in_source\n15550000001,2999-01-01T00:00:00Z,form'])
def test_invalid_csv_is_atomic_and_never_echoes_customer_data(client, tenant, content):
    response = client.post(BASE + '/contacts/import', headers=tenant['headers'], files={'file': ('x.csv', content)})
    assert response.status_code == 422 and '15550000001' not in response.text
    assert client.get(BASE + '/contacts', headers=tenant['headers']).json()['total'] == 0


def test_upload_limits_and_endpoint_validation(client, tenant, monkeypatch):
    headers = tenant['headers']
    monkeypatch.setattr(settings, 'contact_import_max_rows', 1)
    assert upload(client, headers, [{'phone': '15550000001'}, {'phone': '15550000002'}]).status_code == 422
    monkeypatch.setattr(settings, 'contact_import_max_bytes', 8)
    assert upload(client, headers, [{'phone': '15550000001'}]).status_code == 413
    assert client.get(BASE + '/contacts?limit=0', headers=headers).status_code == 422
    assert client.get(BASE + '/segments?offset=-1', headers=headers).status_code == 422
    assert client.get(BASE + '/contacts?segment_id=invalid', headers=headers).status_code == 422
    assert client.put(BASE + '/contacts/' + uid() + '/consent', headers=headers,
                      json={'opted_in': 'yes', 'source': ''}).status_code == 422
    assert client.delete(BASE + '/segments/invalid', headers=headers).status_code == 422
    for method, path in [('post', '/segments'), ('put', '/segments/' + uid())]:
        assert client.request(method, BASE + path, headers=headers,
                              json={'name': 'Invalid', 'filters': {'sql': 'DROP TABLE contacts'}}).status_code == 422
