"""Google Calendar account administration; callbacks bind the initiating cookie session."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from ..services import calendar_availability
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


class BindingInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_id: UUID
    calendar_id: str = Field(min_length=1, max_length=1024)
    service_id: UUID | None = None
    staff_id: UUID | None = None
    confirm_data_sharing: StrictBool

    @model_validator(mode='after')
    def valid(self):
        if not self.confirm_data_sharing or not (self.service_id or self.staff_id):
            raise ValueError('Select a service or staff member and confirm customer-data sharing')
        return self


class BindingView(BaseModel):
    id: str
    connection_id: str
    calendar_id: str
    service_id: str | None
    staff_id: str | None


@router.get('/bindings', response_model=list[BindingView])
def bindings(principal: Principal = Depends(require_role('admin'))):
    return calendar_availability.bindings(principal.tenant.id)


@router.post('/bindings', status_code=201, response_model=dict[str, str])
def bind(body: BindingInput, principal: Principal = Depends(require_role('admin'))):
    return {'id': run(calendar_availability.bind, principal.tenant.id, body.model_dump(mode='json'))}


@router.delete('/bindings/{identity}', status_code=204)
def unbind(identity: UUID, principal: Principal = Depends(require_role('admin'))):
    run(calendar_availability.unbind, principal.tenant.id, str(identity))
