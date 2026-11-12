"""Owner/admin store setup. Customer lookups are available only to conversation-bound tools."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..integrations.commerce import StoreInput
from ..integrations.http_actions.transport import ActionError
from ..services import commerce
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/commerce', tags=['commerce'])


class StoreView(BaseModel):
    id: str
    provider: str
    url: str
    status: str
    source_id: str | None
    product_count: int
    last_success: datetime | None
    last_error: str | None
    next_run: datetime | None
    lease: str | None


def run(p, fn, *args):
    try:
        return fn(p.tenant.id, *args)
    except ActionError as exc:
        raise HTTPException(exc.status or 409, exc.code) from None
    except ValueError:
        raise HTTPException(503, 'Credential encryption unavailable') from None


@router.get('', response_model=list[StoreView])
def listing(p: Principal = Depends(require_role('admin'))):
    return commerce.listing(p.tenant.id)


@router.post('', response_model=StoreView, status_code=201)
def connect(body: StoreInput, p: Principal = Depends(require_role('admin'))):
    return run(p, commerce.connect, body)


@router.post('/{identity}/test', response_model=StoreView)
def test(identity: UUID, p: Principal = Depends(require_role('admin'))):
    return run(p, commerce.test_connection, str(identity))


@router.delete('/{identity}', status_code=204)
def disconnect(identity: UUID, p: Principal = Depends(require_role('admin'))):
    run(p, commerce.disconnect, str(identity))
    return Response(status_code=204)
