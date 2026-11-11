"""Workspace-owned HTTP action configuration; credentials are write-only."""
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import Session, Tenant
from ..integrations.http_actions.contracts import Definition
from ..models.http_actions import HttpAction, HttpActionCall
from ..services import crypto
from ..services.audit import audit
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/http-actions', tags=['custom-actions'])


class ActionView(BaseModel):
    id: str
    name: str
    definition: dict
    credential_last4: str
    revision: int
    tested_revision: int | None
    enabled: bool
    status: str
    last_success_at: datetime | None
    last_error: str | None


class ActionEnable(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool


def owned(db, tenant_id, identity, lock=False):
    query = select(HttpAction).where(HttpAction.tenant_id == tenant_id, HttpAction.id == str(identity))
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise HTTPException(404, 'Custom action not found')
    return row


def view(row):
    return {key: getattr(row, key) for key in ActionView.model_fields}


def split(body, tenant_id):
    values = body.model_dump(mode='json')
    private = {'headers': values.pop('headers'), 'auth': values.pop('auth')}
    values['auth_kind'] = private['auth']['kind']
    values['header_names'] = list(private['headers'])
    secret = (private['auth']['value'] or private['auth']['password'] or private['auth']['client_secret']
              or next(iter(private['headers'].values()), ''))
    try:
        packet = crypto.encrypt(tenant_id, json.dumps(private).encode())
    except ValueError:
        raise HTTPException(503, 'Configure encryption before saving credentials') from None
    return values, packet, secret[-4:]


@router.post('', response_model=ActionView, status_code=201)
def create(body: Definition, p: Principal = Depends(require_role('admin'))):
    values, packet, last4 = split(body, p.tenant.id)
    try:
        with Session.begin() as db:
            db.get(Tenant, p.tenant.id, with_for_update=True)
            if db.scalar(select(func.count()).select_from(HttpAction).where(
                    HttpAction.tenant_id == p.tenant.id)) >= settings.http_action_limit:
                raise HTTPException(409, 'Custom action limit reached')
            row = HttpAction(tenant_id=p.tenant.id, name=body.name, definition=values,
                             credentials_enc=packet, credential_last4=last4)
            db.add(row)
            db.flush()
            audit(db, p.tenant.id, 'http_action.created', row.id, actor_user_id=p.user.id if p.user else None)
            return view(row)
    except IntegrityError:
        raise HTTPException(409, 'An action with this name already exists') from None


@router.get('', response_model=list[ActionView])
def listing(response: Response, p: Principal = Depends(require_role('admin'))):
    response.headers['Cache-Control'] = 'no-store'
    with Session() as db:
        return [view(row) for row in db.scalars(select(HttpAction).where(
            HttpAction.tenant_id == p.tenant.id).order_by(HttpAction.name))]


@router.put('/{identity}', response_model=ActionView)
def update(identity: UUID, body: Definition, p: Principal = Depends(require_role('admin'))):
    with Session() as db:
        owned(db, p.tenant.id, identity)
    values, packet, last4 = split(body, p.tenant.id)
    try:
        with Session.begin() as db:
            row = owned(db, p.tenant.id, identity, True)
            row.name, row.definition, row.credentials_enc, row.credential_last4 = body.name, values, packet, last4
            row.revision += 1
            row.enabled, row.status, row.tested_revision, row.last_error = False, 'needs_test', None, None
            audit(db, p.tenant.id, 'http_action.updated', row.id, actor_user_id=p.user.id if p.user else None)
            return view(row)
    except IntegrityError:
        raise HTTPException(409, 'An action with this name already exists') from None


@router.patch('/{identity}', response_model=ActionView)
def enable(identity: UUID, body: ActionEnable, p: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = owned(db, p.tenant.id, identity, True)
        if body.enabled and (row.tested_revision != row.revision or row.status != 'connected'):
            raise HTTPException(409, 'Successfully test this revision before enabling it')
        row.enabled = body.enabled
        audit(db, p.tenant.id, 'http_action.enabled' if body.enabled else 'http_action.paused', row.id,
              actor_user_id=p.user.id if p.user else None)
        return view(row)


@router.delete('/{identity}', status_code=204)
def remove(identity: UUID, p: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = owned(db, p.tenant.id, identity, True)
        if db.scalar(select(HttpActionCall.id).where(HttpActionCall.tenant_id == p.tenant.id,
                HttpActionCall.action_id == row.id, HttpActionCall.state == 'running').limit(1)):
            raise HTTPException(409, 'Wait for the running action to finish')
        audit(db, p.tenant.id, 'http_action.deleted', row.id, actor_user_id=p.user.id if p.user else None)
        db.delete(row)
