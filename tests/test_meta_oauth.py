"""Real database consent isolation with fake Meta grants and asset catalogs."""
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.api.auth import COOKIE
from app.config import settings
from app.db import Channel, Membership, Session, UserSession, engine, now, uid
from app.models.meta_oauth import MetaAuthorization as Attempt
from app.services import meta_oauth as oauth
from app.services.auth import hash_token
from app.services.crypto import decrypt
from app.services.meta_signup import MetaSignupError
from tests import test_meta_dm_connections as connections

admin = connections.admin
BASE = '/api/v1/meta-dm/oauth'


class Graph(connections.FakeGraph):
    calls = []
    hook = None

    def request(self, path, **kwargs):
        assert engine.pool.checkedout() == 0
        Graph.calls.append((path, kwargs))
        if path == 'oauth/access_token':
            token = 'long-private' if (kwargs.get('params') or {}).get('grant_type') else 'short-private'
            return {'access_token': token}
        if path == 'me/accounts':
            return {'data': [{'id': '888100', 'name': 'Shop Page', 'access_token': 'page-private',
                             'instagram_business_account': {
                                 'id': '888111', 'name': 'Shop Instagram', 'ig_id': 888111}}]}
        result = super().request(path, **kwargs)
        if path.endswith('/subscribed_apps') and kwargs.get('payload') is not None and Graph.hook:
            hook, Graph.hook = Graph.hook, None
            hook()
        return result


@pytest.fixture
def configured(admin, monkeypatch):
    monkeypatch.setattr(settings, 'meta_dm_oauth_redirect_uri', 'http://localhost:3000/api/backend/meta-dm/oauth/callback')
    monkeypatch.setattr(settings, 'meta_dm_oauth_config_id', None)
    monkeypatch.setattr(oauth, 'MetaSignup', Graph)
    Graph.calls, Graph.hook = [], None
    return admin


def begin(client, headers, kind='messenger'):
    response = client.post(BASE + '/start', headers=headers, json={'kind': kind})
    assert response.status_code == 201, response.text
    body = response.json()
    return body['id'], parse_qs(urlparse(body['url']).query)['state'][0]


def approve(client, headers, kind='messenger'):
    identity, state = begin(client, headers, kind)
    response = client.get(BASE + '/callback', headers=headers, params={'state': state, 'code': 'private-code'},
                          follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'private-code' not in response.headers['location']
    return identity, state


@pytest.mark.parametrize(('kind', 'external'), [('messenger', '888100'), ('instagram', '888111')])
def test_authorize_discover_select_and_atomically_connect(client, configured, kind, external):
    identity, state = approve(client, configured, kind)
    assert Graph.calls == []  # No provider I/O in consent handlers.
    with Session() as db:
        row = db.get(Attempt, identity)
        assert row.state_hash is None and b'private-code' not in row.secret_enc
    assert oauth.process_one()
    response = client.get(BASE + '/' + identity, headers=configured)
    assert response.json()['status'] == 'selecting' and 'private' not in response.text
    assert response.json()['accounts'] == [{'external_id': external,
            'name': 'Shop Instagram' if kind == 'instagram' else 'Shop Page', 'kind': kind}]
    assert client.post(BASE + '/' + identity + '/select', headers=configured,
                       json={'external_id': '999999'}).status_code == 422
    result = client.post(BASE + '/' + identity + '/select', headers=configured,
                         json={'external_id': external})
    assert result.status_code == 202
    assert oauth.process_one() and not oauth.process_one()
    result = client.get(BASE + '/' + identity, headers=configured).json()
    assert result['status'] == 'complete' and result['accounts'] == [] and result['channel_id']
    with Session() as db:
        assert db.get(Attempt, identity).secret_enc is None
        channel = db.get(Channel, result['channel_id'])
        assert decrypt(channel.tenant_id, channel.credentials_enc) == b'page-private'
    assert client.get(BASE + '/callback', headers=configured, params={'state': state, 'code': 'reuse'},
                      follow_redirects=False).status_code == 404
    assert client.post(BASE + '/' + identity + '/cancel', headers=configured).status_code == 409


def test_denial_expiry_and_cancellation_wipe_credentials(client, configured):
    identity, state = begin(client, configured)
    response = client.get(BASE + '/callback', headers=configured, params={'state': state, 'error': 'access_denied'},
                          follow_redirects=False)
    assert response.status_code == 303
    assert client.get(BASE + '/' + identity, headers=configured).json()['status'] == 'denied'
    identity, state = begin(client, configured)
    with Session.begin() as db:
        db.get(Attempt, identity).expires_at = now() - timedelta(seconds=1)
    assert client.get(BASE + '/' + identity, headers=configured).json()['status'] == 'expired'
    identity, state = approve(client, configured)
    assert client.post(BASE + '/' + identity + '/cancel', headers=configured).json()['status'] == 'cancelled'
    assert not oauth.process_one() and Graph.calls == []
    with Session() as db:
        assert all(row.secret_enc is None for row in db.scalars(select(Attempt)))


def test_cross_workspace_and_cross_session_cannot_consume_consent(client, configured):
    identity, state = begin(client, configured)
    other = client.post('/api/v1/workspaces', headers=configured, json={'name': 'Other'}).json()['tenant_id']
    foreign = {**configured, 'X-Workspace-Id': other}
    assert client.get(BASE + '/' + identity, headers=foreign).status_code == 404
    assert client.get(BASE + '/callback', headers=foreign, params={'state': state, 'code': 'private'},
                      follow_redirects=False).status_code == 403
    with Session.begin() as db:
        attempt = db.get(Attempt, identity)
        raw = uid()
        db.add(UserSession(id_hash=hash_token(raw), user_id=attempt.user_id, csrf_hash=hash_token('csrf'),
                           mfa_verified=True, expires_at=now() + timedelta(days=1)))
    client.cookies.clear()
    client.cookies.set(COOKIE, raw)
    assert client.get(BASE + '/' + identity, headers=configured).status_code == 403
    assert client.get(BASE + '/callback', headers=configured, params={'state': state, 'code': 'private'},
                      follow_redirects=False).status_code == 403


@pytest.mark.parametrize('change', ['session_expired', 'role_revoked', 'suspended'])
def test_worker_revalidates_before_network(client, configured, change):
    identity, _ = approve(client, configured)
    with Session.begin() as db:
        attempt = db.get(Attempt, identity)
        if change == 'session_expired':
            db.get(UserSession, attempt.session_hash).expires_at = now() - timedelta(seconds=1)
        elif change == 'role_revoked':
            db.scalar(select(Membership).where(Membership.tenant_id == attempt.tenant_id,
                                              Membership.user_id == attempt.user_id)).role = 'viewer'
        else:
            from app.db import Tenant
            db.get(Tenant, attempt.tenant_id).suspended = True
    assert oauth.process_one() and Graph.calls == []
    with Session() as db:
        attempt = db.get(Attempt, identity)
        assert attempt.status == 'failed' and attempt.secret_enc is None


def test_cancelling_during_enrollment_rolls_back_provider_subscription(client, configured):
    identity, _ = approve(client, configured)
    assert oauth.process_one()
    assert client.post(BASE + '/' + identity + '/select', headers=configured,
                       json={'external_id': '888100'}).status_code == 202
    Graph.hook = lambda: client.post(BASE + '/' + identity + '/cancel', headers=configured)
    assert oauth.process_one()
    with Session() as db:
        assert db.get(Attempt, identity).status == 'cancelled'
        assert db.scalar(select(Channel.id).where(Channel.tenant_id == configured['X-Workspace-Id'])) is None
    assert any(args.get('method') == 'DELETE' for path, args in Graph.calls)


def test_provider_failure_is_terminal_and_does_not_replay_code(client, configured, monkeypatch):
    identity, _ = approve(client, configured)
    def unavailable(*args, **kwargs):
        raise MetaSignupError('private code/provider error')
    monkeypatch.setattr(Graph, 'request', unavailable)
    assert oauth.process_one() and not oauth.process_one()
    response = client.get(BASE + '/' + identity, headers=configured)
    assert response.json()['error'] == 'provider_failed' and 'private' not in response.text
    with Session() as db:
        assert db.get(Attempt, identity).secret_enc is None


def test_endpoint_auth_role_isolation_and_validation(client, configured, tenant):
    response = client.get(BASE + '/config', headers=configured)
    assert response.json() == {'configured': True} and response.headers['cache-control'] == 'no-store'
    assert client.post(BASE + '/start', headers=tenant['headers'], json={'kind': 'messenger'}).status_code == 403
    assert client.post(BASE + '/start', headers=configured, json={'kind': 'whatsapp'}).status_code == 422
    assert client.post(BASE + '/start', headers=configured,
                       json={'kind': 'messenger', 'tenant_id': tenant['tenant_id']}).status_code == 422
    assert client.get(BASE + '/not-a-uuid', headers=configured).status_code == 422
    assert client.post(BASE + '/not-a-uuid/cancel', headers=configured).status_code == 422
    assert client.post(BASE + '/not-a-uuid/select', headers=configured,
                       json={'external_id': '123'}).status_code == 422
    identity, state = begin(client, configured)
    assert client.get(BASE + '/callback', headers=configured, params={'state': state},
                      follow_redirects=False).status_code == 422
    assert client.post(BASE + '/' + identity + '/select', headers=configured,
                       json={'external_id': '../unsafe'}).status_code == 422
    for method, path, body in [('GET','/config',None), ('GET','/' + uid(),None),
                              ('POST','/' + uid() + '/select',{'external_id':'123'}),
                              ('POST','/' + uid() + '/cancel',None)]:
        if path != '/config':
            assert client.request(method, BASE + path, headers=configured, json=body).status_code == 404
    with Session.begin() as db:
        attempt = db.get(Attempt, identity)
        db.scalar(select(Membership).where(Membership.tenant_id == attempt.tenant_id,
                                          Membership.user_id == attempt.user_id)).role = 'viewer'
    assert client.get(BASE + '/config', headers=configured).status_code == 403
    assert client.post(BASE + '/' + identity + '/select', headers=configured,
                       json={'external_id': '123'}).status_code == 403
    assert client.post(BASE + '/' + identity + '/cancel', headers=configured).status_code == 403
    assert client.get(BASE + '/callback', headers=configured, params={'state': state, 'code': 'private'},
                      follow_redirects=False).status_code == 403
    client.cookies.clear()
    assert client.post(BASE + '/start', json={'kind': 'messenger'}).status_code == 401
    assert client.get(BASE + '/config').status_code == 401
    assert client.get(BASE + '/' + identity).status_code == 401
    assert client.post(BASE + '/' + identity + '/select', json={'external_id': '123'}).status_code == 401
    assert client.post(BASE + '/' + identity + '/cancel').status_code == 401
    assert client.get(BASE + '/callback', params={'state': state, 'code': 'private'},
                      follow_redirects=False).status_code == 401


def test_configuration_and_authorization_attempt_budget(client, configured, monkeypatch):
    monkeypatch.setattr(settings, 'meta_dm_oauth_redirect_uri', 'http://untrusted.example/callback')
    assert client.get(BASE + '/config', headers=configured).json() == {'configured': False}
    assert client.post(BASE + '/start', headers=configured, json={'kind': 'messenger'}).status_code == 503
    monkeypatch.setattr(settings, 'meta_dm_oauth_redirect_uri', 'https://example.com/api/backend/meta-dm/oauth/callback')
    monkeypatch.setattr(settings, 'meta_dm_oauth_config_id', 'business-config')
    first = client.post(BASE + '/start', headers=configured, json={'kind': 'instagram'}).json()
    params = parse_qs(urlparse(first['url']).query)
    assert params['config_id'] == ['business-config'] and 'scope' not in params
    assert params['response_type'] == ['code'] and params['override_default_response_type'] == ['true']
    assert params['auth_type'] == ['rerequest']
    begin(client, configured)
    begin(client, configured)
    assert client.post(BASE + '/start', headers=configured, json={'kind': 'messenger'}).status_code == 409


def test_stale_claim_is_failed_without_replaying_a_code(client, configured):
    identity, _ = approve(client, configured)
    with Session.begin() as db:
        row = db.get(Attempt, identity)
        row.status, row.claim_token = 'discovering', uid()
        row.claimed_at = now() - timedelta(minutes=6)
    assert not oauth.process_one() and Graph.calls == []
    with Session() as db:
        row = db.get(Attempt, identity)
        assert row.status == 'failed' and row.last_error == 'interrupted' and row.secret_enc is None


@pytest.mark.parametrize('too_many', [False, True])
def test_empty_or_oversized_catalogs_never_publish_partial_credentials(client, configured, monkeypatch, too_many):
    identity, _ = approve(client, configured)
    original = Graph.request
    def catalog(self, path, **kwargs):
        if path == 'me/accounts':
            return {'data': [{'id': str(i + 1), 'name': 'Page', 'access_token': 'private'}
                             for i in range(101 if too_many else 0)]}
        return original(self, path, **kwargs)
    monkeypatch.setattr(Graph, 'request', catalog)
    assert oauth.process_one()
    response = client.get(BASE + '/' + identity, headers=configured).json()
    assert response['status'] == 'failed' and response['accounts'] == []
    assert response['error'] == ('provider_failed' if too_many else 'no_accounts')
    with Session() as db:
        assert db.get(Attempt, identity).secret_enc is None
