from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import ApiKey, Campaign, CampaignRecipient, Contact, ContactSegment, Session, Template, now, uid
from app.services.channels import whatsapp_channel
from tests.test_contacts import upload
from tests.test_whatsapp import link_number

BASE = '/api/v1/campaigns'


@pytest.fixture
def draft(client, tenant):
    headers = tenant['headers']
    link_number(client, tenant)
    upload(client, headers, [{'phone': '15550000001', 'name': 'Asha', 'opt_in_at': '2026-01-01T00:00:00Z',
                             'opt_in_source': 'form'}, {'phone': '15550000002', 'name': 'No consent'}])
    with Session.begin() as db:
        channel = whatsapp_channel(db, tenant['tenant_id'])
        channel.waba_id = '123'
        template = Template(id=uid(), channel_id=channel.id, meta_id='campaign-template', name='offer',
                            language='en', category='MARKETING', status='APPROVED', body='Hello {{1}}')
        segment = ContactSegment(id=uid(), tenant_id=tenant['tenant_id'], name='All', filters={})
        db.add_all([template, segment])
    body = {'name': 'Offers', 'template_id': template.id, 'segment_id': segment.id,
            'variables': [{'field': 'name'}], 'unit_price': '0.25'}
    response = client.post(BASE, headers=headers, json=body)
    assert response.status_code == 201, response.text
    return tenant, body, response.json()


def test_preview_test_confirmation_schedule_and_cancel(client, draft):
    tenant, _, campaign = draft
    headers, path = tenant['headers'], BASE + '/' + campaign['id']
    assert (campaign['recipients'], campaign['excluded']) == (1, 1)
    assert Decimal(campaign['estimated_max_cost']) == Decimal('0.25')
    assert campaign['preview'][0]['text'] == 'Hello Asha'
    assert not campaign['test_accepted'] and campaign['state'] == 'draft'
    assert client.post(path + '/start', headers=headers, json={'confirm': True}).status_code == 409
    contact_id = campaign['preview'][0]['contact_id']
    test = client.post(path + '/test', headers=headers, json={'contact_id': contact_id, 'own_number': True})
    assert test.status_code == 202
    assert client.get(path, headers=headers).json()['test_state'] == 'queued'
    again = client.post(path + '/test', headers=headers, json={'contact_id': contact_id, 'own_number': True})
    assert again.json() == test.json()
    with Session.begin() as db:
        db.get(CampaignRecipient, test.json()['id']).state = 'sent'
    when = (now() + timedelta(hours=1)).isoformat()
    started = client.post(path + '/start', headers=headers, json={'confirm': True, 'scheduled_at': when})
    assert started.status_code == 200 and started.json()['state'] == 'scheduled'
    repeated = client.post(path + '/start', headers=headers, json={'confirm': True})
    assert datetime.fromisoformat(repeated.json()['scheduled_at']) == datetime.fromisoformat(when)
    assert client.get(BASE, headers=headers).json()[0]['id'] == campaign['id']
    results = client.get(path + '/recipients', headers=headers).json()
    assert len(results) == 2 and all(row['name'] == 'Asha' and row['phone'] == '15550000001' for row in results)
    assert len(client.get(path + '/recipients?outcome=sent', headers=headers).json()) == 1
    assert client.get(path + '/recipients?outcome=replied', headers=headers).json() == []
    assert client.get(path + '/recipients?outcome=invalid', headers=headers).status_code == 422
    assert client.post(path + '/cancel', headers=headers).json()['state'] == 'cancelled'
    assert client.post(path + '/start', headers=headers, json={'confirm': True}).status_code == 409


@pytest.mark.parametrize('method,suffix,body', [('get', '', None), ('post', '', {}), ('get', '/ID', None),
    ('get', '/ID/recipients', None), ('post', '/ID/test', {'contact_id': uid(), 'own_number': True}),
    ('post', '/ID/start', {'confirm': True}), ('post', '/ID/cancel', None)])
def test_campaign_permissions(client, tenant, method, suffix, body):
    path = BASE + suffix.replace('ID', uid())
    assert client.request(method, path, json=body).status_code == 401
    with Session.begin() as db:
        key = db.scalar(select(ApiKey).where(ApiKey.tenant_id == tenant['tenant_id']))
        key.scopes = ['agent']
    assert client.request(method, path, headers=tenant['headers'], json=body).status_code == 403


def test_campaign_isolation_and_validation(client, draft):
    tenant, body, campaign = draft
    headers, path = tenant['headers'], BASE + '/' + campaign['id']
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    foreign = {'X-Admin-Key': other['admin_key']}
    assert client.get(BASE, headers=foreign).json() == []
    for method, suffix, data in [('get', '', None), ('get', '/recipients', None), ('post', '/cancel', None),
        ('post', '/start', {'confirm': True}), ('post', '/test', {'contact_id': uid(), 'own_number': True})]:
        assert client.request(method, path + suffix, headers=foreign, json=data).status_code == 404
    assert client.post(BASE, headers=foreign, json=body).status_code == 404
    for data in [{**body, 'template_id': uid()}, {**body, 'segment_id': uid()}]:
        assert client.post(BASE, headers=headers, json=data).status_code == 404
    assert client.post(BASE, headers=headers, json={**body, 'variables': []}).status_code == 422
    assert client.post(BASE, headers=headers, json={**body, 'unit_price': '-1'}).status_code == 422
    assert client.post(path + '/test', headers=headers,
                       json={'contact_id': uid(), 'own_number': False}).status_code == 422
    assert client.post(path + '/start', headers=headers, json={'confirm': False}).status_code == 422
    assert client.post(path + '/start', headers=headers,
                       json={'confirm': True, 'scheduled_at': '2020-01-01T00:00:00Z'}).status_code == 422
    for endpoint in [BASE + '?limit=0', path + '/recipients?offset=-1', BASE + '/invalid']:
        assert client.get(endpoint, headers=headers).status_code == 422
    with Session.begin() as db:
        template = db.get(Template, body['template_id'])
        template.status = 'PAUSED'
    contact_id = campaign['preview'][0]['contact_id']
    assert client.post(path + '/test', headers=headers,
                       json={'contact_id': contact_id, 'own_number': True}).status_code == 409
    with Session() as db:
        assert db.scalar(select(Campaign).where(Campaign.tenant_id == tenant['tenant_id'])).state == 'draft'
        assert len(db.scalars(select(Contact).where(Contact.tenant_id == tenant['tenant_id'])).all()) == 2
