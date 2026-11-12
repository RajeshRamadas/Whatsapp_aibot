"""Encrypted configuration, OAuth session binding and per-endpoint authorization."""
import base64
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import ApiKey, Membership, Session, User, UserSession, now, uid
from app.models.knowledge_sync import KnowledgeConnection as Connection
from app.services import knowledge_connections as service
from app.services.auth import hash_password, hash_token

BASE = '/api/v1/knowledge-sync'
NOTION = {'label': 'Policies', 'token': 'notion-private-token', 'pages': ['11111111-1111-4111-8111-111111111111'],
          'read_only': True}


@pytest.fixture
def sync_setup(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b's' * 32).decode())
    monkeypatch.setattr(settings, 'knowledge_google_client_id', 'fake-google-client')
    monkeypatch.setattr(settings, 'knowledge_google_client_secret', 'fake-google-secret')
    monkeypatch.setattr(settings, 'knowledge_google_redirect_uri', 'http://localhost/api/backend/knowledge-sync/callback')
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    raw, csrf, user_id = uid(), uid(), uid()
    with Session.begin() as db:
        db.add(User(id=user_id, email='knowledge-owner@example.test', name='Admin', email_verified_at=now(),
                    password_hash=hash_password('test-password')))
        db.flush()
        db.add(Membership(tenant_id=tenant['tenant_id'], user_id=user_id, role='owner'))
        db.add(UserSession(id_hash=hash_token(raw), user_id=user_id, csrf_hash=hash_token(csrf),
                           expires_at=now() + timedelta(days=1)))
    client.cookies.set('chatbot_session', raw)
    client.cookies.set('chatbot_csrf', csrf)
    return {'X-Workspace-Id': tenant['tenant_id'], 'X-CSRF-Token': csrf}, user_id


def test_notion_configuration_roles_validation_isolation(client, tenant, sync_setup):
    headers, _ = sync_setup
    response = client.post(BASE + '/notion', headers=headers, json=NOTION)
    assert response.status_code == 201, response.text
    identity = response.json()['id']
    assert 'notion-private-token' not in response.text
    with Session() as db:
        row = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        assert b'notion-private-token' not in row.credentials_enc
    assert client.post(BASE + '/notion', headers=headers, json=NOTION).status_code == 409
    for change in ({'pages': []}, {'pages': ['not-a-page']}, {'read_only': False}, {'read_only': 'true'},
                   {'token': 'short'}, {'tenant_id': uid()}):
        assert client.post(BASE + '/notion', headers=headers, json=NOTION | change).status_code == 422
    assert client.post(BASE + '/invalid', headers=headers, json={'action': 'sync'}).status_code == 422
    assert client.post(BASE + '/' + identity, headers=headers, json={'action': 'other'}).status_code == 422
    assert client.delete(BASE + '/invalid', headers=headers).status_code == 422
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other knowledge'}).json()
    foreign = {'Authorization': 'Bearer ' + other['admin_key']}
    assert client.get(BASE, headers=foreign).json()['connections'] == []
    for method, path, body in [('POST', '/' + identity, {'action': 'pause'}), ('DELETE', '/' + identity, None)]:
        assert client.request(method, BASE + path, headers=foreign, json=body).status_code == 404
    key = uid()
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], key_hash=hash_token(key), key_prefix=key[:8],
                      name='Viewer', scopes=['viewer']))
    for method, path, body in [('GET', '', None), ('POST', '/notion', NOTION),
                              ('POST', '/drive', {'label': 'Drive', 'folders': ['testfolder123']}),
                              ('POST', '/' + identity, {'action': 'sync'}), ('DELETE', '/' + identity, None)]:
        assert client.request(method, BASE + path, headers={'Authorization': 'Bearer ' + key},
                              json=body).status_code == 403
        client.cookies.clear()
        assert client.request(method, BASE + path, json=body).status_code == 401
    for action in ('pause', 'resume', 'sync'):
        assert client.post(BASE + '/' + identity, headers=tenant['headers'], json={'action': action}).status_code == 202
    assert client.delete(BASE + '/' + identity, headers=tenant['headers']).status_code == 204


def test_drive_consent_read_only_pkce_and_session_binding(client, tenant, sync_setup):
    headers, user_id = sync_setup
    body = {'label': 'Drive policies', 'folders': ['testfolder123']}
    assert client.post(BASE + '/drive', headers=tenant['headers'], json=body).status_code == 403
    assert client.post(BASE + '/drive', headers=headers, json=body | {'folders': ['invalid/path']}).status_code == 422
    response = client.post(BASE + '/drive', headers=headers, json=body)
    assert response.status_code == 200, response.text
    params = parse_qs(urlsplit(response.json()['url']).query)
    assert params['scope'] == [service.SCOPE] and params['code_challenge_method'] == ['S256']
    state = params['state'][0]
    assert client.get(BASE + '/callback', params={'state': state}).status_code == 422
    assert client.get(BASE + '/callback', params={'state': 'invalid'}).status_code == 422
    # A different signed-in session cannot consume the capability.
    session = client.cookies.get('chatbot_session')
    client.cookies.set('chatbot_session', uid())
    assert client.get(BASE + '/callback', params={'state': state, 'code': 'fake'}).status_code == 401
    client.cookies.set('chatbot_session', session)
    response = client.get(BASE + '/callback', params={'state': state, 'code': 'fake'}, follow_redirects=False)
    assert response.status_code == 303 and 'fake' not in response.headers['location']
    assert client.get(BASE + '/callback', params={'state': state, 'code': 'fake'}).status_code == 404
    with Session() as db:
        row = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        assert row.status == 'connecting' and b'fake' not in row.oauth_secret_enc
