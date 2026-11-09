"""Contact imports, consent records and saved dynamic segments."""
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import Contact, ContactSegment, Session, now
from ..services import contacts as service
from ..services.audit import audit
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1', tags=['contacts'])


class ContactView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    channel: str
    external_user: str
    name: str | None
    language: str | None
    attributes: dict[str, Any]
    opt_in_at: datetime | None
    opt_out_at: datetime | None
    consent_source: str | None
    marketing_eligible: bool


class ContactPage(BaseModel):
    items: list[ContactView]
    total: int
    limit: int
    offset: int


class ConsentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opted_in: StrictBool
    source: service.Source


class ImportResult(BaseModel):
    processed: int
    created: int
    updated: int
    consents_recorded: int


class SegmentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    filters: service.SegmentFilters


class SegmentView(SegmentInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime
    updated_at: datetime


class SegmentPage(BaseModel):
    items: list[SegmentView]
    total: int
    limit: int
    offset: int


def scoped(db, model, identity, tenant_id, *, lock=False):
    query = select(model).where(model.id == str(identity), model.tenant_id == tenant_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, 'Record not found')
    return row


def view(contact):
    return ContactView.model_validate({**contact.__dict__, 'marketing_eligible': service.marketing_eligible(contact)})


@router.get('/contacts', response_model=ContactPage)
def list_contacts(segment_id: UUID | None = None, search: str = Query('', max_length=200),
                  opted_in: bool | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  principal: Principal = Depends(require_role('agent'))):
    with Session() as db:
        filters = (service.SegmentFilters.model_validate(scoped(db, ContactSegment, segment_id,
                   principal.tenant.id).filters) if segment_id else service.SegmentFilters(eligible_only=False))
        query = service.contact_query(principal.tenant.id, filters)
        if search:
            query = query.where(or_(Contact.name.icontains(search, autoescape=True),
                                     Contact.external_user.icontains(search, autoescape=True)))
        if opted_in is not None:
            query = query.where(service.eligible() if opted_in else ~service.eligible())
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        items = [view(row) for row in db.scalars(query.order_by(Contact.id).offset(offset).limit(limit))]
    return ContactPage(items=items, total=total, limit=limit, offset=offset)


@router.post('/contacts/import', response_model=ImportResult)
async def import_csv(file: UploadFile = File(...), principal: Principal = Depends(require_role('admin'))):
    content = await file.read(settings.contact_import_max_bytes + 1)
    if len(content) > settings.contact_import_max_bytes:
        raise HTTPException(413, 'CSV exceeds the contact upload limit')
    try:
        return await run_in_threadpool(service.import_contacts, content, principal.tenant.id,
                                      principal.user.id if principal.user else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.put('/contacts/{contact_id}/consent', response_model=ContactView)
def set_consent(contact_id: UUID, body: ConsentInput, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        contact = scoped(db, Contact, contact_id, principal.tenant.id, lock=True)
        if contact.channel != 'whatsapp':
            raise HTTPException(409, 'WhatsApp contact required')
        service.record_consent(db, contact, body.opted_in, now(), body.source, origin='operator',
                               actor_user_id=principal.user.id if principal.user else None)
        return view(contact)


@router.get('/segments', response_model=SegmentPage)
def list_segments(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  principal: Principal = Depends(require_role('agent'))):
    with Session() as db:
        query = select(ContactSegment).where(ContactSegment.tenant_id == principal.tenant.id)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        items = [SegmentView.model_validate(row) for row in db.scalars(
            query.order_by(ContactSegment.name, ContactSegment.id).offset(offset).limit(limit))]
    return SegmentPage(items=items, total=total, limit=limit, offset=offset)


@router.post('/segments', status_code=201, response_model=SegmentView)
def create_segment(body: SegmentInput, principal: Principal = Depends(require_role('admin'))):
    try:
        with Session.begin() as db:
            row = ContactSegment(tenant_id=principal.tenant.id, name=body.name, filters=body.filters.model_dump())
            db.add(row)
            db.flush()
            audit(db, principal.tenant.id, 'segment.created', row.id,
                  actor_user_id=principal.user.id if principal.user else None,
                  actor_type='user' if principal.user else 'api_key')
            result = SegmentView.model_validate(row)
    except IntegrityError:
        raise HTTPException(409, 'A segment with this name already exists') from None
    return result


@router.put('/segments/{segment_id}', response_model=SegmentView)
def update_segment(segment_id: UUID, body: SegmentInput, principal: Principal = Depends(require_role('admin'))):
    try:
        with Session.begin() as db:
            row = scoped(db, ContactSegment, segment_id, principal.tenant.id, lock=True)
            row.name, row.filters, row.updated_at = body.name, body.filters.model_dump(), now()
            audit(db, principal.tenant.id, 'segment.updated', row.id,
                  actor_user_id=principal.user.id if principal.user else None,
                  actor_type='user' if principal.user else 'api_key')
            result = SegmentView.model_validate(row)
    except IntegrityError:
        raise HTTPException(409, 'A segment with this name already exists') from None
    return result


@router.delete('/segments/{segment_id}', status_code=204)
def delete_segment(segment_id: UUID, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = scoped(db, ContactSegment, segment_id, principal.tenant.id, lock=True)
        audit(db, principal.tenant.id, 'segment.deleted', row.id,
              actor_user_id=principal.user.id if principal.user else None,
              actor_type='user' if principal.user else 'api_key')
        db.delete(row)
