"""Workspace lifecycle and team invitations."""
import json
import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from ..db import Invitation, Membership, Session, Tenant, User, now, uid
from ..services import auth as security
from ..services import email
from ..services.audit import audit
from .auth import _csrf, _session
from .principal import Principal, require_role
from .shared import sha, tenant_view

router = APIRouter(prefix='/api/v1', tags=['workspaces'])
Role = Literal['owner', 'admin', 'agent', 'viewer']
InviteRole = Literal['admin', 'agent', 'viewer']


class WorkspaceInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    allowed_origins: list[str] = Field(default_factory=list)


class InviteInput(BaseModel):
    email: EmailStr
    role: InviteRole


class AcceptInput(BaseModel):
    token: str = Field(min_length=20)


class RoleInput(BaseModel):
    role: Role


def _user(request: Request) -> User:
    row, user = _session(request)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        _csrf(request, row)
    return user


@router.post('/workspaces', status_code=201)
def create_workspace(body: WorkspaceInput, user: User = Depends(_user)):
    key = 'adm_' + secrets.token_urlsafe(32)
    tenant = Tenant(id=uid(), name=body.name, admin_key_hash=sha(key),
                    public_key='pub_' + secrets.token_urlsafe(18),
                    allowed_origins=json.dumps(body.allowed_origins))
    with Session.begin() as db:
        db.add(tenant)
        db.flush()
        db.add(Membership(id=uid(), user_id=user.id, tenant_id=tenant.id, role='owner'))
    return tenant_view(tenant)


@router.get('/workspaces')
def list_workspaces(user: User = Depends(_user)):
    with Session() as db:
        rows = db.execute(select(Tenant, Membership.role).join(
            Membership, Membership.tenant_id == Tenant.id).where(
            Membership.user_id == user.id).order_by(Tenant.name)).all()
    return [{**tenant_view(tenant), 'role': role} for tenant, role in rows]


@router.post('/invitations', status_code=201)
def invite(body: InviteInput, principal: Principal = Depends(require_role('admin'))):
    if principal.user is None:
        raise HTTPException(403, 'A user account is required to invite members')
    address = str(body.email).lower()
    raw = security.new_token()
    with Session.begin() as db:
        existing = db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
            User.email == address, Membership.tenant_id == principal.tenant.id))
        if existing:
            raise HTTPException(409, 'Already a workspace member')
        invitation = Invitation(id=uid(), tenant_id=principal.tenant.id, email=address,
                                role=body.role, token_hash=security.hash_token(raw),
                                expires_at=now() + timedelta(days=7), invited_by=principal.user.id)
        db.add(invitation)
        audit(db, principal.tenant.id, 'invitation.created', invitation.id,
              {'role': body.role}, principal.user.id)
    email.sender.send(address, 'Workspace invitation', raw)
    return {'id': invitation.id, 'email': address, 'role': invitation.role, 'expires_at': invitation.expires_at}


@router.get('/invitations')
def list_invitations(principal: Principal = Depends(require_role('admin'))):
    with Session() as db:
        rows = db.scalars(select(Invitation).where(Invitation.tenant_id == principal.tenant.id)).all()
    return [{'id': i.id, 'email': i.email, 'role': i.role, 'expires_at': i.expires_at} for i in rows]


@router.delete('/invitations/{invitation_id}', status_code=204)
def revoke_invitation(invitation_id: str, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        invitation = db.scalar(select(Invitation).where(
            Invitation.id == invitation_id, Invitation.tenant_id == principal.tenant.id))
        if not invitation:
            raise HTTPException(404, 'Invitation not found')
        db.delete(invitation)
        audit(db, principal.tenant.id, 'invitation.revoked', invitation_id,
              actor_user_id=principal.user.id)


@router.post('/invitations/accept')
def accept_invitation(body: AcceptInput, user: User = Depends(_user)):
    with Session.begin() as db:
        invitation = db.scalar(select(Invitation).where(
            Invitation.token_hash == security.hash_token(body.token)).with_for_update())
        if not invitation or invitation.expires_at <= now() or invitation.email != user.email:
            raise HTTPException(400, 'Invalid or expired invitation')
        exists = db.scalar(select(Membership.id).where(
            Membership.user_id == user.id, Membership.tenant_id == invitation.tenant_id))
        if exists:
            raise HTTPException(409, 'Already a workspace member')
        db.add(Membership(id=uid(), user_id=user.id, tenant_id=invitation.tenant_id, role=invitation.role))
        workspace_id = invitation.tenant_id
        db.delete(invitation)
        audit(db, workspace_id, 'invitation.accepted', invitation.id,
              actor_user_id=user.id)
    return {'tenant_id': workspace_id, 'status': 'joined'}


@router.get('/members')
def list_members(principal: Principal = Depends(require_role('admin'))):
    with Session() as db:
        rows = db.execute(select(User, Membership.role).join(
            Membership, Membership.user_id == User.id).where(
            Membership.tenant_id == principal.tenant.id).order_by(User.email)).all()
    return [{'user_id': u.id, 'email': u.email, 'name': u.name, 'role': role} for u, role in rows]


def _change_member(db, principal: Principal, member_id: str):
    member = db.scalar(select(Membership).where(
        Membership.user_id == member_id, Membership.tenant_id == principal.tenant.id).with_for_update())
    if not member:
        raise HTTPException(404, 'Member not found')
    if member.role == 'owner':
        owners = db.scalar(select(func.count()).select_from(Membership).where(
            Membership.tenant_id == principal.tenant.id, Membership.role == 'owner'))
        if owners <= 1:
            raise HTTPException(409, 'Cannot remove the last owner')
    return member


@router.patch('/members/{user_id}')
def change_member_role(user_id: str, body: RoleInput,
                       principal: Principal = Depends(require_role('admin'))):
    if body.role == 'owner' and principal.role != 'owner':
        raise HTTPException(403, 'Only owners can assign owner role')
    with Session.begin() as db:
        member = _change_member(db, principal, user_id)
        if member.role == 'owner' and principal.role != 'owner':
            raise HTTPException(403, 'Only owners can change owner role')
        previous_role = member.role
        member.role = body.role
        audit(db, principal.tenant.id, 'member.role_changed', user_id,
              {'from': previous_role, 'to': body.role}, principal.user.id if principal.user else None)
    return {'user_id': user_id, 'role': body.role}


@router.delete('/members/{user_id}', status_code=204)
def remove_member(user_id: str, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        member = _change_member(db, principal, user_id)
        if member.role == 'owner' and principal.role != 'owner':
            raise HTTPException(403, 'Only owners can remove owners')
        db.delete(member)
        audit(db, principal.tenant.id, 'member.removed', user_id,
              actor_user_id=principal.user.id if principal.user else None)
