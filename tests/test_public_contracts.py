"""Public bearer boundary, durable replay and authenticated cursor contracts."""
import base64
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.shared import sha
from app.config import settings
from app.db import ApiKey, Plan, Session, Source, Tenant, now, uid
from app.models.public_api import ApiRequest
from app.public_api.contracts import PublicError, cursor, decode_cursor, post
from app.rate_limits import RateLimiter

ROOT = '/api/v1/public'


@pytest.fixture
def public(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b'p' * 32).decode())
    RateLimiter(60, namespace='public-api').clear()
    raw, identity = 'sk_live_public_test', uid()
    with Session.begin() as db:
        db.add(ApiKey(id=identity, tenant_id=tenant['tenant_id'], name='Public',
                     key_hash=sha(raw), key_prefix=raw[:20], scopes=['knowledge:read']))
    return {'Authorization': 'Bearer ' + raw}


def test_public_auth_and_quota(client, tenant, public):
    assert client.get(ROOT + '/capabilities').status_code == 401
    assert client.get(ROOT + '/capabilities', headers=tenant['headers']).status_code == 401
    assert client.get(ROOT + '/capabilities', headers=public).json()['scopes'] == ['knowledge:read']
    assert client.get('/api/v1/tenant', headers=public).status_code == 403
    assert client.get(ROOT + '/capabilities', headers={**public, 'X-Workspace-Id': uid()}).status_code == 404
    with Session.begin() as db:
        db.get(Plan, 'starter').limits = {'api_requests_per_minute': 1}
    RateLimiter(60, namespace='public-api').clear()
    assert client.get(ROOT + '/capabilities', headers=public).status_code == 200
    denied = client.get(ROOT + '/capabilities', headers=public)
    assert denied.status_code == 429 and denied.headers['retry-after'] == '60'
    assert denied.json()['error']['details'] == {'limit': 1, 'window_seconds': 60}
    with Session.begin() as db:
        key = db.scalar(select(ApiKey).where(ApiKey.key_hash == sha('sk_live_public_test')))
        key.revoked_at = now()
    assert client.get(ROOT + '/capabilities', headers=public).status_code == 401


def test_cursor_binding_and_deletion(client, tenant, public):
    tid = tenant['tenant_id']
    token = cursor(tid, 'sources', uid(), {'status': 'ready'})
    assert decode_cursor(token, tid, 'sources', {'status': 'ready'})
    for args in [(uid(), 'sources', {'status': 'ready'}), (tid, 'contacts', {'status': 'ready'}),
                 (tid, 'sources', None)]:
        with pytest.raises(PublicError) as error:
            decode_cursor(token, *args)
        assert error.value.status_code == 422
    with pytest.raises(PublicError):
        decode_cursor('broken', tid, 'sources')


def test_post_atomic_replay_and_expiry(client, tenant, public):
    with Session() as db:
        principal = SimpleNamespace(tenant=db.get(Tenant, tenant['tenant_id']))
    def create(db, identity):
        db.add(Source(id=identity, tenant_id=principal.tenant.id, filename='policy.txt',
                      kind='txt', file_path='unused'))
        return 201, {'id': identity}, {'Location': '/sources/' + identity}
    first = post(principal, 'same', '/sources', {'content': 'policy'}, create)
    again = post(principal, 'same', '/sources', {'content': 'policy'}, create)
    assert first.body == again.body and again.headers['idempotency-replayed'] == 'true'
    with pytest.raises(PublicError) as error:
        post(principal, 'same', '/sources', {'content': 'changed'}, create)
    assert error.value.status_code == 409
    with Session.begin() as db:
        assert db.scalar(select(func.count()).select_from(Source)) == 1
        db.scalar(select(ApiRequest)).expires_at = now() - timedelta(seconds=1)
    assert post(principal, 'same', '/sources', {'content': 'changed'}, create).body != first.body
    def rejected(db, identity):
        create(db, identity)
        db.flush()
        raise HTTPException(409, 'Unavailable')
    response = post(principal, 'rejected', '/sources', {}, rejected)
    assert response.status_code == 409
    assert json.loads(post(principal, 'rejected', '/sources', {}, create).body) == json.loads(response.body)
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Source)) == 2


def test_public_json_and_schema(client, public):
    result = client.post(ROOT + '/missing', content='x', headers=public)
    assert result.status_code == 415 and isinstance(result.json()['error']['details'], dict)
    schema = client.get(ROOT + '/openapi.json').json()
    assert schema['components']['securitySchemes']['ApiKey']['scheme'] == 'bearer'
    assert schema['paths']['/capabilities']['get']['security'] == [{'ApiKey': []}]
