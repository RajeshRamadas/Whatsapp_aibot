import httpx

from app import whatsapp
from app.config import settings
from app.db import Channel, Session
from tests.conftest import signed, wa_payload

BASE = '/api/v1/channels'


def test_channel_token_routes_webhook_without_global_token(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    headers = tenant['headers']
    assert client.patch('/api/v1/tenant', headers=headers,
                        json={'whatsapp_phone_number_id': 'PNID1'}).status_code == 200
    [channel] = client.get(BASE, headers=headers).json()
    path = BASE + '/' + channel['id']
    assert channel['status'] == 'configured' and not channel['has_channel_token']
    assert client.patch(path, headers=headers, json={'access_token': 'bad token'}).status_code == 422
    response = client.patch(path, headers=headers, json={'access_token': 'tenant-token', 'display_name': 'Support'})
    assert response.status_code == 200 and response.json()['has_channel_token']
    assert 'tenant-token' not in response.text and 'credentials_enc' not in response.text
    with Session() as db:
        assert b'tenant-token' not in db.get(Channel, channel['id']).credentials_enc

    monkeypatch.setattr(settings, 'whatsapp_access_token', None)
    requests = []

    def post(url, **kwargs):
        requests.append(kwargs['headers']['Authorization'])
        return httpx.Response(200, request=httpx.Request('POST', url), json={'messages': [{'id': 'wamid.out'}]})

    monkeypatch.setattr(whatsapp.httpx, 'post', post)
    body, signature = signed(wa_payload('hello'))
    assert client.post('/webhooks/whatsapp', content=body, headers=signature).status_code == 200
    assert requests == ['Bearer tenant-token']
    status = client.get(path, headers=headers).json()
    assert status['status'] == 'configured' and status['last_webhook_at']

    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other channels'}).json()
    foreign = {'X-Admin-Key': other['admin_key']}
    assert client.get(BASE, headers=foreign).json() == []
    assert client.get(path, headers=foreign).status_code == 404
    assert client.patch(path, headers=foreign, json={'access_token': 'other-token'}).status_code == 404
    assert client.get(BASE).status_code == 401
    assert client.patch(path, headers=headers, json={'access_token': None}).json()['status'] == 'unconfigured'
    assert client.patch('/api/v1/tenant', headers=headers,
                        json={'whatsapp_phone_number_id': None}).status_code == 200
    assert client.get(BASE, headers=headers).json() == []


def test_channel_can_use_legacy_fallback(client, tenant):
    headers = tenant['headers']
    assert client.patch('/api/v1/tenant', headers=headers,
                        json={'whatsapp_phone_number_id': 'PNID2'}).status_code == 200
    assert whatsapp.access_token('PNID2', tenant['tenant_id']) == settings.whatsapp_access_token
