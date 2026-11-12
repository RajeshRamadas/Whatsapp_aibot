"""Owner/admin store controls; secrets are write-only, sync runs in the worker."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, model_validator
from sqlalchemy import select

from ..db import Session
from ..integrations.ecommerce import origin
from ..integrations.http_actions.transport import ActionError
from ..models.ecommerce import StoreConnection
from ..services import ecommerce
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/stores', tags=['ecommerce'])


class StoreInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['shopify', 'woocommerce']
    label: str = Field(min_length=1, max_length=100, pattern=r'\S')
    origin: str = Field(max_length=255)
    token: SecretStr | None = Field(None, min_length=8, max_length=4096)
    key: SecretStr | None = Field(None, min_length=8, max_length=200)
    secret: SecretStr | None = Field(None, min_length=8, max_length=4096)

    @model_validator(mode='after')
    def valid(self):
        self.origin = origin(self.origin, self.provider)
        if self.provider == 'shopify':
            if not self.token or self.key or self.secret:
                raise ValueError('Provide only the Shopify access token')
        elif self.token or not self.key or not self.secret:
            raise ValueError('Provide only the WooCommerce read-only key and secret')
        for name in ('token', 'key', 'secret'):
            value = getattr(self, name)
            if value and (not value.get_secret_value().isascii() or any(
                    ord(c) < 33 or ord(c) == 127 for c in value.get_secret_value())):
                raise ValueError('Credentials cannot contain whitespace or control characters')
        return self


class StoreView(BaseModel):
    id: str
    provider: str
    label: str
    origin: str
    enabled: bool
    status: str
    product_count: int
    last_success_at: datetime | None
    last_error: str | None
    next_sync: datetime | None


class StoreEnabled(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool


def run(fn, *args):
    try:
        return fn(*args)
    except ActionError as exc:
        raise HTTPException(exc.status or 409, {'code': exc.code, 'message': 'Store operation unavailable'}) from None
    except ValueError:
        raise HTTPException(503, 'Store encryption is unavailable') from None


@router.get('', response_model=list[StoreView])
def listing(response: Response, p: Principal = Depends(require_role('admin'))):
    response.headers['Cache-Control'] = 'no-store'
    with Session() as db:
        return [ecommerce.view(r) for r in db.scalars(select(StoreConnection).where(
            StoreConnection.tenant_id == p.tenant.id).order_by(StoreConnection.created_at))]


@router.post('', response_model=StoreView, status_code=201)
def connect(body: StoreInput, p: Principal = Depends(require_role('admin'))):
    credentials = {k: getattr(body, k).get_secret_value() for k in ('token', 'key', 'secret') if getattr(body, k)}
    return run(ecommerce.connect, p.tenant.id, body.provider, body.label, body.origin, credentials)


@router.post('/{identity}/test', response_model=StoreView)
def test(identity: UUID, p: Principal = Depends(require_role('admin'))):
    return run(ecommerce.test, p.tenant.id, str(identity))


@router.patch('/{identity}', response_model=StoreView)
def configure(identity: UUID, body: StoreEnabled, p: Principal = Depends(require_role('admin'))):
    return run(ecommerce.configure, p.tenant.id, str(identity), body.enabled)


@router.post('/{identity}/sync', response_model=StoreView, status_code=202)
def sync(identity: UUID, p: Principal = Depends(require_role('admin'))):
    return run(ecommerce.request_sync, p.tenant.id, str(identity))


@router.delete('/{identity}', status_code=204)
def remove(identity: UUID, p: Principal = Depends(require_role('admin'))):
    run(ecommerce.remove, p.tenant.id, str(identity))
