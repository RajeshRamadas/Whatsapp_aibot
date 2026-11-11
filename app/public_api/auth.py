"""Bearer-only integration auth and per-workspace, plan-based quotas."""
from dataclasses import dataclass

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError
from sqlalchemy import select

from ..api.shared import sha
from ..config import settings
from ..db import ApiKey, Plan, Session, Tenant, now
from ..observability import bind
from ..rate_limits import RateLimiter
from ..services.public_scopes import permissions
from .contracts import PublicError

bearer = HTTPBearer(auto_error=False, scheme_name='ApiKey', description='A workspace API key; never a session cookie.')


@dataclass(frozen=True)
class PublicPrincipal:
    tenant: Tenant
    key_id: str
    scopes: frozenset[str]
    limit: int


def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                 workspace: str | None = Header(None, alias='X-Workspace-Id')):
    if not credentials or credentials.scheme.lower() != 'bearer':
        raise PublicError(401, 'unauthorized', 'A bearer API key is required', headers={'WWW-Authenticate': 'Bearer'})
    with Session.begin() as db:
        key = db.scalar(select(ApiKey).where(ApiKey.key_hash == sha(credentials.credentials)).with_for_update())
        if not key or key.revoked_at or key.expires_at and key.expires_at <= now():
            raise PublicError(401, 'unauthorized', 'API key is invalid, expired or revoked')
        tenant = db.get(Tenant, key.tenant_id)
        if not tenant or workspace and tenant.id != workspace:
            raise PublicError(404, 'not_found', 'Workspace not found')
        if tenant.suspended:
            raise PublicError(403, 'workspace_suspended', 'Workspace is suspended')
        scopes = permissions(key.scopes)
        if not scopes:
            raise PublicError(403, 'insufficient_scope', 'This key has no public API permissions')
        plan = db.get(Plan, tenant.plan_id)
        configured = plan.limits.get('api_requests_per_minute') if plan else None
        limit = (min(6000, max(1, configured)) if type(configured) is int
                 else settings.public_api_default_requests_per_minute)
        key.last_used_at = now()
        result = PublicPrincipal(tenant, key.id, scopes, limit)
    bind(tenant_id=tenant.id)
    try:
        allowed = RateLimiter(limit, namespace='public-api').allow(tenant.id)
    except RedisError:
        raise PublicError(503, 'rate_limit_unavailable', 'API quota service is unavailable',
                          headers={'Retry-After': '60'}) from None
    if not allowed:
        raise PublicError(429, 'rate_limited', 'Workspace API quota exceeded',
                          details={'limit': limit, 'window_seconds': 60}, headers={'Retry-After': '60'})
    return result


def require(scope):
    def check(principal: PublicPrincipal = Depends(authenticate)):
        if scope not in principal.scopes:
            raise PublicError(403, 'insufficient_scope', 'The API key does not allow this action',
                              details={'required_scope': scope})
        return principal
    return check
