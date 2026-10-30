"""Workspace principal resolution and role checks."""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, Response
from sqlalchemy import select

from ..db import ApiKey, Membership, Session, Tenant, User, now
from .auth import _csrf, _session
from .shared import sha

LEVELS = {'viewer': 0, 'agent': 1, 'admin': 2, 'owner': 3}


@dataclass(frozen=True)
class Principal:
    user: User | None
    tenant: Tenant
    role: str
    legacy_key: bool = False


def current_principal(
    request: Request,
    workspace_id: str | None = Header(default=None, alias='X-Workspace-Id'),
    admin_key: str | None = Header(default=None, alias='X-Admin-Key'),
    response: Response = None,
) -> Principal:
    credential = None
    legacy = False
    authorization = request.headers.get('Authorization')
    if authorization is not None:
        scheme, _, value = authorization.partition(' ')
        if scheme.lower() != 'bearer' or not value.strip():
            raise HTTPException(401, 'Invalid bearer authorization')
        credential = value.strip()
    elif admin_key:
        credential = admin_key
        legacy = True
    if credential:
        with Session.begin() as db:
            key = db.scalar(select(ApiKey).where(ApiKey.key_hash == sha(credential)).with_for_update())
            if not key or key.revoked_at or (key.expires_at and key.expires_at <= now()):
                raise HTTPException(401, 'Invalid or revoked API key')
            tenant = db.get(Tenant, key.tenant_id)
            if not tenant or (workspace_id and workspace_id != tenant.id):
                raise HTTPException(404, 'Workspace not found')
            key.last_used_at = now()
            scopes = key.scopes
        if legacy:
            response.headers['Warning'] = '299 - "X-Admin-Key is deprecated; use Authorization: Bearer"'
        role = max(scopes, key=lambda item: LEVELS[item]) if scopes else 'viewer'
        return Principal(user=None, tenant=tenant, role=role, legacy_key=legacy)
    _, user = _session(request)
    if not workspace_id:
        raise HTTPException(400, 'X-Workspace-Id required')
    with Session() as db:
        membership = db.scalar(select(Membership).where(
            Membership.user_id == user.id, Membership.tenant_id == workspace_id))
        if not membership:
            raise HTTPException(404, 'Workspace not found')
        tenant = db.get(Tenant, workspace_id)
        if not tenant:
            raise HTTPException(404, 'Workspace not found')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        row, _ = _session(request)
        _csrf(request, row)
    return Principal(user=user, tenant=tenant, role=membership.role)


def require_role(role: str):
    def check(principal: Principal = Depends(current_principal)) -> Principal:
        if LEVELS[principal.role] < LEVELS[role]:
            raise HTTPException(403, 'Insufficient workspace permissions')
        return principal
    return check


def workspace_tenant(principal: Principal = Depends(require_role('viewer'))) -> Tenant:
    return principal.tenant


def tenant_for_role(role: str):
    def check(principal: Principal = Depends(require_role(role))) -> Tenant:
        return principal.tenant
    return check
