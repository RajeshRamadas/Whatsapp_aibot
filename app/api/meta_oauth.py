"""Meta consent handlers persist work; provider calls run in the worker."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from ..integrations import meta_oauth as provider
from ..services import meta_oauth as service
from ..services.meta_connections import ConnectionError
from .auth import _session
from .meta_dm_connections import GraphId, admin
from .principal import Principal

router = APIRouter(prefix='/api/v1/meta-dm/oauth', tags=['meta-dm-oauth'])


class MetaOAuthStart(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['instagram', 'messenger']
    channel_id: UUID | None = None


class MetaOAuthURL(BaseModel):
    id: str
    url: str


class MetaOAuthAccount(BaseModel):
    external_id: str
    name: str
    kind: Literal['instagram', 'messenger']


class MetaOAuthView(BaseModel):
    id: str
    kind: Literal['instagram', 'messenger']
    status: str
    channel_id: str | None
    error: str | None
    accounts: list[MetaOAuthAccount]


class MetaOAuthSelect(BaseModel):
    model_config = ConfigDict(extra='forbid')
    external_id: GraphId


def actor(request: Request, response: Response, principal: Principal = Depends(admin)):
    session, user = _session(request)
    response.headers['Cache-Control'] = 'no-store'
    return service.Actor(principal.tenant.id, user.id, session.id_hash)


def run(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except ConnectionError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except (ValueError, SQLAlchemyError):
        raise HTTPException(503, 'Authorization data is unavailable; start authorization again') from None


@router.get('/config', response_model=dict[str, bool])
def config(who: service.Actor = Depends(actor)):
    return {'configured': provider.configured()}


@router.post('/start', response_model=MetaOAuthURL, status_code=201)
def start(body: MetaOAuthStart, who: service.Actor = Depends(actor)):
    return run(service.start, who, body.kind, str(body.channel_id) if body.channel_id else None)


@router.get('/callback', response_class=RedirectResponse)
def callback(request: Request, state: Annotated[str, Query(min_length=40, max_length=128)],
             code: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
             error: Annotated[str | None, Query(min_length=1, max_length=100)] = None):
    session, user = _session(request)
    if not code and not error:
        raise HTTPException(422, 'Authorization code or denial is required')
    identity = run(service.callback, state, code or '', user.id, session.id_hash,
                   workspace=request.headers.get('X-Workspace-Id'), denied=bool(error))
    return RedirectResponse('/dashboard/channels?meta_oauth=' + identity, status_code=303,
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


@router.get('/{identity}', response_model=MetaOAuthView)
def status(identity: UUID, who: service.Actor = Depends(actor)):
    return run(service.status, who, str(identity))


@router.post('/{identity}/select', response_model=MetaOAuthView, status_code=202)
def select(identity: UUID, body: MetaOAuthSelect, who: service.Actor = Depends(actor)):
    return run(service.select_account, who, str(identity), body.external_id)


@router.post('/{identity}/cancel', response_model=MetaOAuthView)
def cancel(identity: UUID, who: service.Actor = Depends(actor)):
    return run(service.cancel, who, str(identity))
