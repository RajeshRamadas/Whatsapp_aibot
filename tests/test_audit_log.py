"""Audit log records security actions without leaking secrets across workspaces."""
from app.api import auth
from app.config import settings


def test_audit_log_lifecycle(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    auth._auth_hits.clear()
    assert client.post('/api/v1/auth/signup', json={
        'email': 'auditor@example.com', 'name': 'Auditor', 'password': 'strong-passphrase-123'
    }).status_code == 201
    assert client.post('/api/v1/auth/login', json={
        'email': 'auditor@example.com', 'password': 'strong-passphrase-123'
    }).status_code == 200
    csrf = {'X-CSRF-Token': client.cookies['chatbot_csrf']}
    workspace = client.post('/api/v1/workspaces', json={'name': 'Audit Workspace'}, headers=csrf)
    assert workspace.status_code == 201, workspace.text
    headers = {**csrf, 'X-Workspace-Id': workspace.json()['tenant_id']}
    assert client.get('/api/v1/audit-log').status_code == 400
    assert client.get('/api/v1/audit-log', headers=headers).status_code == 200
    assert client.get('/api/v1/audit-log', headers={**headers,
                                                  'X-Workspace-Id': 'missing'}).status_code == 404
    assert client.get('/api/v1/audit-log', headers=headers,
                      params={'limit': 0}).status_code == 422
    created = client.post('/api/v1/api-keys', headers=headers,
                          json={'name': 'Audit key', 'scope': 'viewer'})
    assert created.status_code == 201, created.text
    key_id = created.json()['id']
    raw = created.json()['key']
    assert client.delete(f'/api/v1/api-keys/{key_id}', headers=headers).status_code == 204
    assert client.patch('/api/v1/tenant', headers=headers,
                        json={'name': 'Renamed Workspace'}).status_code == 200
    events = client.get('/api/v1/audit-log', headers=headers).json()
    actions = [event['action'] for event in events]
    assert 'api_key.created' in actions
    assert 'api_key.revoked' in actions
    assert 'workspace.settings_updated' in actions
    assert raw not in str(events)
    assert client.get('/api/v1/audit-log', headers=headers,
                      params={'limit': 1}).status_code == 200
    assert len(client.get('/api/v1/audit-log', headers=headers,
                          params={'limit': 1}).json()) == 1
    assert client.get('/api/v1/audit-log', headers={
        'Authorization': f'Bearer {raw}'}).status_code == 401
