"""Google Calendar account administration; callbacks bind the initiating cookie session."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from ..services import google_calendar as service
from ..services.appointments import AppointmentError
from .appointments import Confirm
from .auth import _session
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/google-calendar', tags=['google-calendar'])


class CalendarAccount(BaseModel):
    id: str
    label: str
    status: str
    calendars: list[dict[str, str]]
    last_success_at: datetime | None
    last_error: str | None


class CalendarAccounts(BaseModel):
    configured: bool
    connections: list[CalendarAccount]


class ConnectInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=100, pattern=r'\S')


class ConnectURL(BaseModel):
    url: str


def run(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except AppointmentError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.get('', response_model=CalendarAccounts)
def listing(principal: Principal = Depends(require_role('admin'))):
    return service.listing(principal.tenant.id)


@router.post('/connect', response_model=ConnectURL)
def connect(body: ConnectInput, request: Request, principal: Principal = Depends(require_role('admin'))):
    if not principal.user:
        raise HTTPException(403, 'Sign in as a workspace administrator to authorize Google')
    session, user = _session(request)
    return run(service.start, principal.tenant.id, user.id, session.id_hash, body.label.strip())


@router.get('/callback', response_class=RedirectResponse)
def callback(request: Request, state: Annotated[str, Query(min_length=40, max_length=128)],
             code: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
             error: Annotated[str | None, Query(max_length=100)] = None):
    session, user = _session(request)
    if not code and not error:
        raise HTTPException(422, 'Authorization code or denial is required')
    run(service.callback, state, code or '', session.id_hash, user.id, denied=bool(error))
    return RedirectResponse('/dashboard/appointments', status_code=303, headers={'Cache-Control': 'no-store',
                                                                              'Referrer-Policy': 'no-referrer'})


@router.post('/connections/{identity}/refresh', response_model=CalendarAccount)
def refresh(identity: str, principal: Principal = Depends(require_role('admin'))):
    return run(service.change, principal.tenant.id, identity)


@router.delete('/connections/{identity}', response_model=CalendarAccount)
def disconnect(identity: str, body: Confirm, principal: Principal = Depends(require_role('admin'))):
    return run(service.change, principal.tenant.id, identity, disconnect=True)
