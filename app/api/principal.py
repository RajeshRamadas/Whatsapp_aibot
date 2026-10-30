"""Workspace principal resolution and role checks."""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select

from ..db import Membership, Session, Tenant, User
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
) -> Principal:
    # Legacy keys remain valid until API-key migration (T1.5).
    if admin_key:
        with Session() as db:
            tenant = db.scalar(select(Tenant).where(Tenant.admin_key_hash == sha(admin_key)))
        if not tenant:
            raise HTTPException(401, 'Invalid admin key')
        if workspace_id and workspace_id != tenant.id:
            raise HTTPException(404, 'Workspace not found')
        return Principal(user=None, tenant=tenant, role='owner', legacy_key=True)
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
