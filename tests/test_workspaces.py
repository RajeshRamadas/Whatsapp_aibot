"""Workspace permissions and invitation lifecycle."""
from app.api import auth
from app.config import settings
from app.services import email


class Recorder(email.EmailSender):
    def __init__(self):
        self.sent = []

    def send(self, address, subject, body):
        self.sent.append((address, subject, body))


def login(client, address, recorder):
    assert client.post('/api/v1/auth/signup', json={
        'email': address, 'name': 'Workspace User', 'password': 'strong-passphrase-123'
    }).status_code == 201
    assert client.post('/api/v1/auth/login', json={
        'email': address, 'password': 'strong-passphrase-123'
    }).status_code == 200
    return {'X-CSRF-Token': client.cookies['chatbot_csrf']}


def test_workspace_roles_and_invitation(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    recorder = Recorder()
    monkeypatch.setattr(email, 'sender', recorder)
    auth._auth_hits.clear()
    owner_headers = login(client, 'owner@demo.example', recorder)
    created = client.post('/api/v1/workspaces', json={'name': 'Demo Workspace'}, headers=owner_headers)
    assert created.status_code == 201, created.text
    tenant_id = created.json()['tenant_id']
    owner_headers['X-Workspace-Id'] = tenant_id
    assert client.get('/api/v1/tenant', headers=owner_headers).status_code == 200
    assert client.get('/api/v1/workspaces').json()[0]['role'] == 'owner'
    invited = client.post('/api/v1/invitations', json={
        'email': 'viewer@demo.example', 'role': 'viewer'
    }, headers=owner_headers)
    assert invited.status_code == 201, invited.text
    token = recorder.sent[-1][2]
    assert token not in invited.text

    client.cookies.clear()
    viewer_headers = login(client, 'viewer@demo.example', recorder)
    accepted = client.post('/api/v1/invitations/accept', json={'token': token}, headers=viewer_headers)
    assert accepted.status_code == 200, accepted.text
    assert client.post('/api/v1/invitations/accept', json={'token': token},
                       headers=viewer_headers).status_code == 400
    viewer_headers['X-Workspace-Id'] = tenant_id
    assert client.get('/api/v1/tenant', headers=viewer_headers).status_code == 200
    assert client.patch('/api/v1/tenant', json={'name': 'Not allowed'},
                        headers=viewer_headers).status_code == 403
    assert client.get('/api/v1/sources', headers=viewer_headers).status_code == 403
    assert client.get('/api/v1/conversations', headers=viewer_headers).status_code == 200
    assert client.get('/api/v1/tenant', headers={**viewer_headers,
                                                'X-Workspace-Id': 'missing'}).status_code == 404
    assert client.get('/api/v1/tenant').status_code == 400
    assert client.post('/api/v1/workspaces', json={'name': 'X'},
                       headers=viewer_headers).status_code == 422

    client.cookies.clear()
    owner_headers = {'X-Workspace-Id': tenant_id, **login_existing(client, 'owner@demo.example')}
    members = client.get('/api/v1/members', headers=owner_headers).json()
    owner_id = next(m['user_id'] for m in members if m['role'] == 'owner')
    assert client.delete(f'/api/v1/members/{owner_id}', headers=owner_headers).status_code == 409


def login_existing(client, address):
    response = client.post('/api/v1/auth/login', json={
        'email': address, 'password': 'strong-passphrase-123'
    })
    assert response.status_code == 200, response.text
    return {'X-CSRF-Token': client.cookies['chatbot_csrf']}
