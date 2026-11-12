"""Workspace admin setup; only workers contact Drive or Notion."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, model_validator

from ..integrations.knowledge_sync.http import SyncError
from ..services import knowledge_connections as service
from .auth import _session
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/knowledge-sync', tags=['knowledge-sync'])
DriveID = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{10,128}$')]


class DriveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=100, pattern=r'\S')
    folders: list[DriveID] = Field(min_length=1, max_length=5)


class NotionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=100, pattern=r'\S')
    pages: list[UUID] = Field(min_length=1, max_length=20)
    token: SecretStr = Field(min_length=16, max_length=4096)
    read_only: StrictBool

    @model_validator(mode='after')
    def valid(self):
        if not self.read_only or any(c in self.token.get_secret_value() for c in '\r\n'):
            raise ValueError('Use a read-content-only Notion connection')
        return self


class ChangeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['sync', 'pause', 'resume']


class ConnectionView(BaseModel):
    id: str
    provider: str
    label: str
    selection: list[str]
    enabled: bool
    status: str
    last_success: datetime | None
    last_error: str | None
    next_run: datetime | None
    lease: str | None


class SourceView(BaseModel):
    id: str
    filename: str
    status: str
    sync_connection_id: str


class KnowledgeSyncCatalog(BaseModel):
    drive_configured: bool
    connections: list[ConnectionView]
    sources: list[SourceView]


class URLView(BaseModel):
    url: str


def run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except SyncError as exc:
        raise HTTPException(exc.status, exc.code) from None
    except ValueError:
        raise HTTPException(503, 'Credential encryption unavailable') from None


@router.get('', response_model=KnowledgeSyncCatalog)
def listing(response: Response, p: Principal = Depends(require_role('admin'))):
    response.headers['Cache-Control'] = 'no-store'
    return service.listing(p.tenant.id)


@router.post('/drive', response_model=URLView)
def drive(body: DriveInput, request: Request, p: Principal = Depends(require_role('admin'))):
    if not p.user:
        raise HTTPException(403, 'Sign in to authorize Google Drive')
    session, user = _session(request)
    return run(service.create, p.tenant.id, 'drive', body.label, list(dict.fromkeys(body.folders)),
               user_id=user.id, session_hash=session.id_hash)


@router.get('/callback', response_class=RedirectResponse)
def callback(request: Request, state: Annotated[str, Query(min_length=40, max_length=128)],
             code: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
             error: Annotated[str | None, Query(max_length=100)] = None):
    if not code and not error:
        raise HTTPException(422, 'Authorization code or denial required')
    session, user = _session(request)
    run(service.callback, state, code or '', session.id_hash, user.id, denied=bool(error))
    return RedirectResponse('/dashboard/knowledge?view=sync', status_code=303,
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


@router.post('/notion', response_model=ConnectionView, status_code=201)
def notion(body: NotionInput, p: Principal = Depends(require_role('admin'))):
    return run(service.create, p.tenant.id, 'notion', body.label,
               list(dict.fromkeys(str(v) for v in body.pages)), body.token.get_secret_value())


@router.post('/{identity}', response_model=ConnectionView, status_code=202)
def change(identity: UUID, body: ChangeInput, p: Principal = Depends(require_role('admin'))):
    return run(service.change, p.tenant.id, str(identity),
               enabled=False if body.action == 'pause' else True if body.action == 'resume' else None)


@router.delete('/{identity}', status_code=204)
def disconnect(identity: UUID, p: Principal = Depends(require_role('admin'))):
    run(service.change, p.tenant.id, str(identity), disconnect=True)
    return Response(status_code=204)
