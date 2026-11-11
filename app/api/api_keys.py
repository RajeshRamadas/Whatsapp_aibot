"""Workspace API-key management. Raw secrets are returned only on creation."""
import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from ..db import ApiKey, Session, now, uid
from ..services.audit import audit
from ..services.public_scopes import PublicScope
from .principal import Principal, require_role
from .shared import sha

router = APIRouter(prefix='/api/v1/api-keys', tags=['api-keys'])
Scope = Literal['viewer', 'agent', 'admin']


class KeyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    scope: Scope | None = None
    scopes: list[PublicScope] | None = Field(None, min_length=1, max_length=11)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode='after')
    def one_scope_mode(self):
        if (self.scope is None) == (self.scopes is None):
            raise ValueError('Choose a role scope or explicit public API scopes')
        if self.scopes and len(self.scopes) != len(set(self.scopes)):
            raise ValueError('Scopes must be unique')
        return self


def view(key: ApiKey) -> dict:
    return {'id': key.id, 'name': key.name, 'key_prefix': key.key_prefix,
            'scopes': key.scopes, 'last_used_at': key.last_used_at,
            'revoked_at': key.revoked_at, 'expires_at': key.expires_at}


@router.post('', status_code=201)
def create_key(body: KeyInput, principal: Principal = Depends(require_role('admin'))):
    if principal.user is None:
        raise HTTPException(403, 'A user session is required to manage API keys')
    secret = 'sk_live_' + secrets.token_urlsafe(32)
    key = ApiKey(id=uid(), tenant_id=principal.tenant.id, name=body.name,
                 key_prefix=secret[:20], key_hash=sha(secret), scopes=body.scopes or [body.scope],
                 expires_at=now() + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    with Session.begin() as db:
        db.add(key)
        audit(db, principal.tenant.id, 'api_key.created', key.id,
              {'name': body.name, 'scopes': key.scopes}, principal.user.id)
    return {**view(key), 'key': secret}


@router.get('')
def list_keys(principal: Principal = Depends(require_role('admin'))):
    with Session() as db:
        keys = db.scalars(select(ApiKey).where(ApiKey.tenant_id == principal.tenant.id)
                          .order_by(ApiKey.name)).all()
    return [view(key) for key in keys]


@router.delete('/{key_id}', status_code=204)
def revoke_key(key_id: str, principal: Principal = Depends(require_role('admin'))):
    if principal.user is None:
        raise HTTPException(403, 'A user session is required to manage API keys')
    with Session.begin() as db:
        key = db.scalar(select(ApiKey).where(ApiKey.id == key_id,
                         ApiKey.tenant_id == principal.tenant.id).with_for_update())
        if not key:
            raise HTTPException(404, 'API key not found')
        key.revoked_at = now()
        audit(db, principal.tenant.id, 'api_key.revoked', key.id,
              actor_user_id=principal.user.id)
