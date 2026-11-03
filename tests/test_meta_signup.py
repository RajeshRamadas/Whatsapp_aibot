import pytest

from app.api import meta_signup
from app.config import settings
from app.services.channels import access_token
from app.services.meta_signup import MetaSignupError
from tests.test_workspaces import Recorder, login

BASE = '/api/v1/whatsapp/signup'
PAYLOAD = {'code': 'fake-code', 'waba_id': '100', 'phone_number_id': '200', 'pin': '123456'}


@pytest.fixture
def admin(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    monkeypatch.setattr(settings, 'whatsapp_app_id', '123')
    monkeypatch.setattr(settings, 'whatsapp_signup_config_id', '456')
    monkeypatch.setattr(settings, 'master_key', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    headers = login(client, 'signup-meta@example.com', Recorder())
    response = client.post('/api/v1/workspaces', headers=headers, json={'name': 'Meta test'})
    assert response.status_code == 201
    return {**headers, 'X-Workspace-Id': response.json()['tenant_id']}


class FakeGraph:
    def __init__(self):
        self.calls = []

    def exchange(self, code):
        self.calls.append(('exchange', code))
        return 'fake-private-token'

    def verify_phone(self, waba, phone, token):
        self.calls.append(('verify', waba, phone))
        return 'Example Business'

    def subscribe(self, waba, token):
        self.calls.append(('subscribe', waba))

    def register(self, phone, pin, token):
        self.calls.append(('register', phone, pin))

    def send_test(self, phone, recipient, token):
        self.calls.append(('test', phone, recipient, token))


def test_embedded_signup_connects_and_sends_test(client, admin, monkeypatch):
    graph = FakeGraph()
    monkeypatch.setattr(meta_signup, 'MetaSignup', lambda: graph)
    assert client.get(BASE + '/config', headers=admin).json()['config_id'] == '456'
    response = client.post(BASE, headers=admin, json=PAYLOAD)
    assert response.status_code == 200, response.text
    assert response.json()['display_name'] == 'Example Business'
    assert response.json()['external_id'] == '200'
    assert 'fake-private-token' not in response.text
    assert access_token('200', admin['X-Workspace-Id']) == 'fake-private-token'
    assert graph.calls == [('exchange', 'fake-code'), ('verify', '100', '200'),
                           ('subscribe', '100'), ('register', '200', '123456')]
    result = client.post(BASE + '/test-message', headers=admin, json={'recipient': '+15550001234'})
    assert result.json() == {'status': 'accepted'}
    assert graph.calls[-1] == ('test', '200', '15550001234', 'fake-private-token')
    assert client.post(BASE, headers=admin, json={**PAYLOAD, 'phone_number_id': '300'}).status_code == 409


def test_signup_failure_leaves_channel_unlinked(client, admin, monkeypatch):
    graph = FakeGraph()

    def reject(*args):
        raise MetaSignupError('Meta rejected the request')

    monkeypatch.setattr(graph, 'register', reject)
    monkeypatch.setattr(meta_signup, 'MetaSignup', lambda: graph)
    assert client.post(BASE, headers=admin, json=PAYLOAD).status_code == 502
    assert client.get('/api/v1/channels', headers=admin).json() == []


def test_signup_auth_and_validation(client, admin, tenant):
    assert client.post(BASE, headers=admin, json={**PAYLOAD, 'phone_number_id': '../bad'}).status_code == 422
    assert client.post(BASE, headers=tenant['headers'], json=PAYLOAD).status_code == 403
    assert client.post(BASE, headers={**admin, 'X-Workspace-Id': tenant['tenant_id']}, json=PAYLOAD).status_code == 404
    client.cookies.clear()
    assert client.post(BASE, json=PAYLOAD).status_code == 401
