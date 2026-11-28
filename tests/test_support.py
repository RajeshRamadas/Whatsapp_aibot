from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.db import AuditLog, Session, User, now
from app.models.support import SupportSession
from app.services import support
from tests.test_platform_admin import admin as admin

START = '/api/v1/platform/tenants/{id}/support-session'
END = '/api/v1/platform/support-session'


def test_support_grant_is_scoped_read_only_and_audited(client, tenant, admin):
    identity = tenant['tenant_id']
    scope = {**admin, 'X-Workspace-Id': identity}
    assert client.get('/api/v1/tenant', headers=scope).status_code == 404
    assert client.post(START.format(id='invalid'), headers=admin).status_code == 422
    assert client.post(START.format(id=uuid4()), headers=admin).status_code == 404
    assert client.post(START.format(id=identity)).status_code == 403
    created = client.post(START.format(id=identity), headers=admin)
    assert created.status_code == 201
    assert created.json()['tenant_id'] == identity
    assert 'HttpOnly' in created.headers['set-cookie']
    [workspace] = client.get('/api/v1/workspaces').json()
    assert workspace['role'] == 'viewer' and workspace['support_expires_at']
    view = client.get('/api/v1/tenant', headers=scope)
    assert view.status_code == 200 and view.headers['cache-control'] == 'no-store'
    assert client.get('/api/v1/tenant', headers={**scope, 'X-Workspace-Id': str(uuid4())}).status_code == 404
    assert client.patch('/api/v1/tenant', headers=scope, json={'name': 'Blocked'}).status_code == 403
    assert client.get('/api/v1/events', headers=scope).status_code == 403
    assert client.get(f'/api/v1/conversations/{uuid4()}/media/{uuid4()}', headers=scope).status_code == 403
    assert client.delete(END, headers=admin).status_code == 204
    assert client.get('/api/v1/tenant', headers=scope).status_code == 404
    with Session() as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.tenant_id == identity,
            AuditLog.action.startswith('support.')).order_by(AuditLog.created_at)).all()
        assert rows[0].action == 'support.started' and rows[-1].action == 'support.ended'
        assert any(row.action == 'support.read' and row.details == {'route': '/api/v1/tenant'} for row in rows)
        assert all(row.actor_type == 'platform_admin' and row.actor_user_id for row in rows)


def test_support_expiry_and_platform_revocation(client, tenant, admin):
    identity = tenant['tenant_id']
    scope = {**admin, 'X-Workspace-Id': identity}
    path = START.format(id=identity)
    assert client.post(path, headers=admin).status_code == 201
    raw = client.cookies[support.COOKIE]
    with Session.begin() as db:
        row = db.scalar(select(SupportSession).where(SupportSession.token_hash == support.hash_token(raw)))
        row.expires_at = now() - timedelta(seconds=1)
    assert client.get('/api/v1/tenant', headers=scope).status_code == 404
    assert client.post(path, headers=admin).status_code == 201
    with Session.begin() as db:
        db.scalar(select(User).where(User.email == 'platform@example.com')).is_platform_admin = False
    assert client.get('/api/v1/tenant', headers=scope).status_code == 404
    assert client.post(path, headers=admin).status_code == 403
    assert client.delete(END, headers=admin).status_code == 403
