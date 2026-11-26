import pytest

from app.api import meta_dm_connections
from app.config import settings
from app.db import Channel, Session
from app.services.crypto import decrypt
from app.services.meta_signup import MetaSignupError
from tests.test_workspaces import Recorder, login

BASE = '/api/v1/meta-dm/channels'
PAYLOAD = {'kind': 'messenger', 'external_id': '12345', 'access_token': 'secret-page-token'}


@pytest.fixture
def admin(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    monkeypatch.setattr(settings, 'master_key', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    monkeypatch.setattr(settings, 'whatsapp_app_secret', 'test-app-secret')
    monkeypatch.setattr(settings, 'whatsapp_verify_token', 'test-verify')
    headers = login(client, 'meta-dm-admin@example.com', Recorder())
    response = client.post('/api/v1/workspaces', headers=headers, json={'name': 'Meta DM test'})
    assert response.status_code == 201
    return {**headers, 'X-Workspace-Id': response.json()['tenant_id']}


class FakeGraph:
    def __init__(self):
        self.calls = []

    def request(self, path, *, params=None, token=None, payload=None):
        self.calls.append((path, params, token, payload))
        if path.endswith('/subscribed_apps'):
            return {'success': True}
        return {'id': path, 'name': 'Example Page'}


def test_connect_verified_and_disconnect(client, admin, monkeypatch):
    graph = FakeGraph()
    monkeypatch.setattr(meta_dm_connections, 'MetaSignup', lambda: graph)
    result = client.post(BASE, headers=admin, json=PAYLOAD)
    assert result.status_code == 201, result.text
    channel_id = result.json()['id']
    assert result.json()['status'] == 'configured'
    assert result.json()['kind'] == 'messenger'
    assert result.json()['display_name'] == 'Example Page'
    assert 'secret-page-token' not in result.text
    with Session() as db:
        row = db.get(Channel, channel_id)
        assert b'secret-page-token' not in row.credentials_enc
        assert decrypt(row.tenant_id, row.credentials_enc).decode() == 'secret-page-token'
    assert graph.calls == [
        ('12345', {'fields': 'id,name'}, 'secret-page-token', None),
        ('12345/subscribed_apps', {'subscribed_fields': 'messages'}, 'secret-page-token', {}),
    ]
    assert client.post(BASE, headers=admin, json=PAYLOAD).status_code == 409
    assert client.delete(BASE + '/' + channel_id, headers=admin).status_code == 204
    assert client.get('/api/v1/channels', headers=admin).json() == []


def test_connect_rejects_unverified_assets_and_bad_inputs(client, admin, monkeypatch):
    graph = FakeGraph()
    monkeypatch.setattr(meta_dm_connections, 'MetaSignup', lambda: graph)
    assert client.post(BASE, headers=admin, json={**PAYLOAD, 'external_id': '../bad'}).status_code == 422
    assert client.post(BASE, headers=admin, json={**PAYLOAD, 'access_token': 'bad token'}).status_code == 422
    monkeypatch.setattr(graph, 'request', lambda *args, **kwargs: {'id': 'not-owned'})
    assert client.post(BASE, headers=admin, json=PAYLOAD).status_code == 502
    with Session() as db:
        assert db.query(Channel).filter_by(kind='messenger').count() == 0
    monkeypatch.setattr(graph, 'request', lambda *args, **kwargs: (_ for _ in ()).throw(
        MetaSignupError('bad secret data')))
    assert 'bad secret data' not in client.post(BASE, headers=admin, json=PAYLOAD).text


def test_connect_authorization_and_isolation(client, admin, tenant, monkeypatch):
    monkeypatch.setattr(meta_dm_connections, 'MetaSignup', FakeGraph)
    assert client.post(BASE, headers=tenant['headers'], json=PAYLOAD).status_code == 403
    assert client.post(BASE, headers={**admin, 'X-Workspace-Id': tenant['tenant_id']},
                       json=PAYLOAD).status_code == 404
    response = client.post(BASE, headers=admin, json=PAYLOAD)
    assert response.status_code == 201
    assert client.delete(BASE + '/' + response.json()['id'], headers=tenant['headers']).status_code == 403
    client.cookies.clear()
    assert client.post(BASE, json=PAYLOAD).status_code == 401
