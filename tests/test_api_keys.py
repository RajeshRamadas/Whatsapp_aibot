"""Scoped API key lifecycle and workspace isolation."""
from app.api import auth
from app.config import settings


def test_api_keys(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    auth._auth_limiter.clear()
    assert client.post('/api/v1/auth/signup', json={
        'email': 'keys@example.com', 'name': 'Keys User', 'password': 'strong-passphrase-123'
    }).status_code == 201
    assert client.post('/api/v1/auth/login', json={
        'email': 'keys@example.com', 'password': 'strong-passphrase-123'
    }).status_code == 200
    csrf = {'X-CSRF-Token': client.cookies['chatbot_csrf']}
    created = client.post('/api/v1/workspaces', json={'name': 'Key Workspace'}, headers=csrf)
    assert created.status_code == 201, created.text
    workspace = created.json()['tenant_id']
    headers = {**csrf, 'X-Workspace-Id': workspace}
    assert client.post('/api/v1/api-keys', json={'name': 'bad', 'scope': 'owner'},
                       headers=headers).status_code == 422
    response = client.post('/api/v1/api-keys', json={
        'name': 'Read only', 'scope': 'viewer', 'expires_in_days': 30
    }, headers=headers)
    assert response.status_code == 201, response.text
    raw = response.json()['key']
    assert raw.startswith('sk_live_')
    key_id = response.json()['id']
    listing = client.get('/api/v1/api-keys', headers=headers)
    assert listing.status_code == 200
    assert raw not in listing.text
    bearer = {'Authorization': f'Bearer {raw}', 'X-Workspace-Id': workspace}
    assert client.get('/api/v1/tenant', headers=bearer).status_code == 200
    assert client.patch('/api/v1/tenant', headers=bearer,
                        json={'name': 'Denied'}).status_code == 403
    assert client.get('/api/v1/tenant', headers={**bearer,
                                                'X-Workspace-Id': 'another'}).status_code == 404
    assert client.delete(f'/api/v1/api-keys/{key_id}', headers=headers).status_code == 204
    assert client.get('/api/v1/tenant', headers=bearer).status_code == 401
    assert client.get('/api/v1/tenant', headers={
        'Authorization': 'Bearer sk_live_invalid'}).status_code == 401


def test_granular_key_creation(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    assert client.post('/api/v1/auth/signup', json={
        'email': 'scopes@example.com', 'name': 'Scopes', 'password': 'strong-passphrase-123'
    }).status_code == 201
    assert client.post('/api/v1/auth/login', json={
        'email': 'scopes@example.com', 'password': 'strong-passphrase-123'
    }).status_code == 200
    headers = {'X-CSRF-Token': client.cookies['chatbot_csrf']}
    workspace = client.post('/api/v1/workspaces', json={'name': 'Scoped'}, headers=headers).json()['tenant_id']
    headers['X-Workspace-Id'] = workspace
    for invalid in [{}, {'scope': 'admin', 'scopes': ['contacts:read']}, {'scopes': []},
                    {'scopes': ['unknown']}, {'scopes': ['contacts:read', 'contacts:read']}]:
        assert client.post('/api/v1/api-keys', json={'name': 'Invalid', **invalid}, headers=headers).status_code == 422
    created = client.post('/api/v1/api-keys', json={'name': 'CRM', 'scopes': ['contacts:read'], 'expires_in_days': 30},
                          headers=headers)
    assert created.status_code == 201 and created.headers['cache-control'] == 'no-store'
    raw = created.json()['key']
    bearer = {'Authorization': 'Bearer ' + raw}
    assert client.get('/api/v1/public/contacts', headers=bearer).status_code == 200
    assert client.get('/api/v1/public/sources', headers=bearer).status_code == 403
    assert client.get('/api/v1/tenant', headers=bearer).status_code == 403
    assert raw not in client.get('/api/v1/api-keys', headers=headers).text
    assert client.delete('/api/v1/api-keys/' + created.json()['id'], headers=headers).status_code == 204
    assert client.get('/api/v1/public/contacts', headers=bearer).status_code == 401
