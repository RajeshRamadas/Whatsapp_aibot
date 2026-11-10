"""OAuth session binding and durable retries against fake Google responses."""
import base64
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import ApiKey, Membership, Session, User, UserSession, engine, now, uid
from app.integrations.google_calendar import SCOPES, CalendarError, Client
from app.models.google_calendar import GoogleConnection as Connection
from app.models.google_calendar import GoogleTask as Task
from app.services import crypto, google_jobs
from app.services.auth import hash_password, hash_token

BASE = '/api/v1/google-calendar'


@pytest.fixture
def connected(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b'c' * 32).decode())
    monkeypatch.setattr(settings, 'google_calendar_client_id', 'fake-client')
    monkeypatch.setattr(settings, 'google_calendar_client_secret', 'fake-secret')
    monkeypatch.setattr(settings, 'google_calendar_redirect_uri', 'http://localhost/api/backend/google-calendar/callback')
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    raw, csrf, user_id = uid(), uid(), uid()
    with Session.begin() as db:
        db.add(User(id=user_id, email='calendar@example.test', name='Administrator',
                    password_hash=hash_password('test-password'), email_verified_at=now()))
        db.flush()
        db.add(Membership(tenant_id=tenant['tenant_id'], user_id=user_id, role='owner'))
        db.add(UserSession(id_hash=hash_token(raw), user_id=user_id, csrf_hash=hash_token(csrf),
                           expires_at=now() + timedelta(days=1)))
    client.cookies.set('chatbot_session', raw)
    client.cookies.set('chatbot_csrf', csrf)
    headers = {'X-Workspace-Id': tenant['tenant_id'], 'X-CSRF-Token': csrf}
    calls = []

    def fake(self, method, path, **kwargs):
        assert engine.pool.checkedout() == 0, 'Provider I/O held a DB connection'
        calls.append((method, path, kwargs))
        if 'token' in path:
            return {'access_token': 'fake-access', 'refresh_token': 'fake-refresh', 'expires_in': 3600,
                    'scope': ' '.join(SCOPES)}
        if 'revoke' in path:
            return {}
        return {'items': [{'id': 'team@example.test', 'summary': 'Team calendar', 'accessRole': 'owner'}]}

    monkeypatch.setattr(Client, 'request', fake)
    return headers, calls, user_id


def authorize(client, headers):
    response = client.post(BASE + '/connect', headers=headers, json={'label': 'Appointments'})
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()['url']).query)
    assert query['code_challenge_method'] == ['S256'] and set(query['scope'][0].split()) == set(SCOPES)
    state = query['state'][0]
    response = client.get(BASE + '/callback', params={'state': state, 'code': 'fake-code'}, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert 'fake-code' not in response.headers['location']
    return state


def test_oauth_encrypt_refresh_disconnect(client, tenant, connected):
    headers, calls, _ = connected
    state = authorize(client, headers)
    assert not calls
    assert google_jobs.process_one()
    result = client.get(BASE, headers=headers).json()
    connection = result['connections'][0]
    assert connection['status'] == 'connected' and connection['calendars'][0]['name'] == 'Team calendar'
    assert 'fake-access' not in str(result) and 'credentials_enc' not in str(result)
    with Session.begin() as db:
        value = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        assert b'fake-refresh' not in value.credentials_enc and value.oauth_secret_enc is None
        assert b'fake-refresh' in crypto.decrypt(tenant['tenant_id'], value.credentials_enc)
        value.token_expires_at = now()
    assert client.get(BASE + '/callback', params={'state': state, 'code': 'fake-code'}).status_code == 404
    identity = connection['id']
    assert client.post(BASE + '/connections/' + identity + '/refresh', headers=headers).status_code == 200
    assert google_jobs.process_one()
    assert any(c[2].get('data', {}).get('grant_type') == 'refresh_token' for c in calls)
    assert client.request('DELETE', BASE + '/connections/' + identity, headers=headers,
                         json={'confirm': False}).status_code == 422
    assert client.request('DELETE', BASE + '/connections/' + identity, headers=headers,
                          json={'confirm': True}).status_code == 200
    assert google_jobs.process_one()
    assert client.get(BASE, headers=headers).json()['connections'][0]['status'] == 'disconnected'


def test_oauth_roles_validation_and_session_binding(client, tenant, connected):
    headers, _, user_id = connected
    for path in ('/connect', '/connections/' + uid() + '/refresh'):
        assert client.post(BASE + path, headers=headers, json={'label': ''}).status_code in (404, 422)
    assert client.post(BASE + '/connect', headers=tenant['headers'], json={'label': 'Team'}).status_code == 403
    assert client.request('DELETE', BASE + '/connections/' + uid(), headers=headers,
                          json={'confirm': True}).status_code == 404
    assert client.post(BASE + '/connect', headers=headers,
                       json={'label': 'Team', 'tenant_id': uid()}).status_code == 422
    response = client.post(BASE + '/connect', headers=headers, json={'label': 'Team'})
    state = parse_qs(urlsplit(response.json()['url']).query)['state'][0]
    with Session.begin() as db:
        membership = db.scalar(select(Membership).where(Membership.user_id == user_id))
        membership.role = 'viewer'
    assert client.get(BASE, headers=headers).status_code == 403
    assert client.get(BASE + '/callback', params={'state': state, 'code': 'fake'}).status_code == 403
    assert client.get(BASE + '/callback', params={'state': 'short'}).status_code == 422
    client.cookies.clear()
    assert client.get(BASE).status_code == 401
    assert client.post(BASE + '/connect', json={'label': 'Team'}).status_code == 401
    assert client.post(BASE + '/connections/' + uid() + '/refresh').status_code == 401
    assert client.request('DELETE', BASE + '/connections/' + uid(), json={'confirm': True}).status_code == 401


def test_oauth_catalog_retry_does_not_replay_code(client, tenant, connected, monkeypatch):
    headers, calls, _ = connected
    authorize(client, headers)
    original = Client.test
    monkeypatch.setattr(Client, 'test', lambda *a: (_ for _ in ()).throw(
        CalendarError('provider_unavailable', retry=True, wait=90)))
    assert google_jobs.process_one()
    with Session.begin() as db:
        task = db.scalar(select(Task).where(Task.tenant_id == tenant['tenant_id']))
        assert task.state == 'queued' and task.next_attempt_at > now() + timedelta(seconds=85)
        task.next_attempt_at = now()
    monkeypatch.setattr(Client, 'test', original)
    assert google_jobs.process_one()
    assert sum(c[1].endswith('/token') for c in calls) == 1
    with Session.begin() as db:
        other = uid()
        db.add(ApiKey(tenant_id=tenant['tenant_id'], key_hash=hash_token(other), key_prefix=other[:8],
                      name='Read only', scopes=['viewer']))
    assert client.get(BASE, headers={'Authorization': 'Bearer ' + other}).status_code == 403
