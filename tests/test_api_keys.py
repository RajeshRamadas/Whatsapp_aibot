"""Scoped API key lifecycle and workspace isolation."""
from app.api import auth
from app.config import settings


def test_api_keys(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    auth._auth_hits.clear()
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
