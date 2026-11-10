"""Authenticated administration of product-managed appointments."""
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator

from ..db import now
from ..services import appointments as service
from ..services.events import publish_sync
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/appointments', tags=['appointments'])
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Identity = Annotated[str, StringConstraints(pattern=r'^[0-9a-fA-F-]{36}$')]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StaffInput(Input):
    name: Name
    active: StrictBool = True


class ServiceInput(StaffInput):
    duration_minutes: int = Field(ge=5, le=480)
    reminder_minutes: int = Field(default=60, ge=5, le=10080)
    reminder_template_id: Identity | None = None


class StaffView(StaffInput):
    id: str


class ServiceView(ServiceInput):
    id: str


class Catalog(BaseModel):
    timezone: str
    services: list[ServiceView]
    staff: list[StaffView]


class SlotInput(Input):
    service_id: Identity
    staff_id: Identity
    starts_at: AwareDatetime


class SlotCreated(SlotInput):
    id: str
    ends_at: datetime


class SlotView(SlotCreated):
    service: str
    staff: str
    state: str
    expires_at: datetime | None


class Hold(Input):
    slot_id: Identity
    conversation_id: Identity
    customer_name: Name
    customer_phone: Annotated[str, StringConstraints(pattern=r'^\+?[1-9][0-9]{6,14}$')] | None = None
    language: Literal['en', 'hi', 'hi-Latn', 'kn', 'ta', 'te', 'mr', 'bn'] | None = None
    reminder_opt_in: StrictBool = False
    replaces_id: Identity | None = None


class BookingView(Hold):
    id: str
    service: str
    staff: str
    starts_at: datetime
    ends_at: datetime
    expires_at: datetime
    state: str
    reminder_state: str
    reminder_error: str | None
    reminder_message_id: int | None


class Confirm(Input):
    confirm: StrictBool

    @field_validator('confirm')
    @classmethod
    def confirmed(cls, value):
        if not value:
            raise ValueError('Explicit confirmation is required')
        return value


def run(principal, action, *args, mutate=False, **kwargs):
    try:
        if mutate:
            kwargs['actor'] = ('user', principal.user.id) if principal.user else ('api_key', None)
        result = action(principal.tenant.id, *args, **kwargs)
    except service.AppointmentError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    if mutate:
        publish_sync(principal.tenant.id, 'appointments.updated', {})
    return result


def window(start, end):
    start = start or now()
    end = end or start + timedelta(days=30)
    if not start < end <= start + timedelta(days=60):
        raise HTTPException(422, 'Choose an ordered date range of at most 60 days')
    return start, end


@router.get('/catalog', response_model=Catalog)
def catalog(principal: Principal = Depends(require_role('agent'))):
    return {'timezone': principal.tenant.timezone, **run(principal, service.catalog)}


@router.post('/services', response_model=ServiceView, status_code=201)
def create_service(body: ServiceInput, principal: Principal = Depends(require_role('admin'))):
    return run(principal, service.save_resource, 'services', body.model_dump(), mutate=True)


@router.put('/services/{identity}', response_model=ServiceView)
def update_service(identity: str, body: ServiceInput, principal: Principal = Depends(require_role('admin'))):
    return run(principal, service.save_resource, 'services', body.model_dump(), identity, mutate=True)


@router.post('/staff', response_model=StaffView, status_code=201)
def create_staff(body: StaffInput, principal: Principal = Depends(require_role('admin'))):
    return run(principal, service.save_resource, 'staff', body.model_dump(), mutate=True)


@router.put('/staff/{identity}', response_model=StaffView)
def update_staff(identity: str, body: StaffInput, principal: Principal = Depends(require_role('admin'))):
    return run(principal, service.save_resource, 'staff', body.model_dump(), identity, mutate=True)


@router.post('/slots', response_model=SlotCreated, status_code=201)
def add_slot(body: SlotInput, principal: Principal = Depends(require_role('admin'))):
    return run(principal, service.add_slot, **body.model_dump(), mutate=True)


@router.delete('/slots/{identity}', status_code=204)
def delete_slot(identity: str, principal: Principal = Depends(require_role('admin'))):
    run(principal, service.remove_slot, identity, mutate=True)
    return Response(status_code=204)


@router.get('/slots', response_model=list[SlotView])
def slots(start: Annotated[AwareDatetime | None, Query()] = None, end: Annotated[AwareDatetime | None, Query()] = None,
          service_id: str | None = None, staff_id: str | None = None,
          principal: Principal = Depends(require_role('agent'))):
    return run(principal, service.slots, *window(start, end), service_id, staff_id)


@router.get('/bookings', response_model=list[BookingView])
def bookings(start: Annotated[AwareDatetime | None, Query()] = None,
             end: Annotated[AwareDatetime | None, Query()] = None, conversation_id: str | None = None,
             principal: Principal = Depends(require_role('agent'))):
    return run(principal, service.bookings, *window(start, end), conversation_id)


@router.post('/bookings', response_model=BookingView, status_code=201)
def hold(body: Hold, principal: Principal = Depends(require_role('agent'))):
    return run(principal, service.hold, **body.model_dump(), mutate=True)


@router.post('/bookings/{identity}/{action}', response_model=BookingView)
def change_booking(identity: str, action: Literal['confirm', 'cancel', 'retry-reminder'], body: Confirm,
                   principal: Principal = Depends(require_role('agent'))):
    return run(principal, service.transition, identity, action, mutate=True)
