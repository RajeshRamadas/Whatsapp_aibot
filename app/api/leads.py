"""Workspace lead configuration, queue and export."""
import csv
import io
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import Conversation, Lead, Membership, Session, Tenant, User
from .principal import tenant_for_role

router = APIRouter(prefix='/api/v1')
LeadField = Literal['name', 'phone', 'need', 'budget', 'location', 'preferred_time']
LeadStatus = Literal['new', 'contacted', 'qualified', 'won', 'lost']
FIELDS = ('name', 'phone', 'need', 'budget', 'location', 'preferred_time')


class LeadConfig(BaseModel):
    fields: dict[LeadField, Literal['required', 'optional']]


class LeadUpdate(BaseModel):
    status: LeadStatus | None = None
    owner_id: str | None = Field(None, min_length=1, max_length=36)
    notes: str | None = Field(None, max_length=8000)


@router.get('/lead-config')
def get_config(tenant: Tenant = Depends(tenant_for_role('admin'))):
    return {'fields': tenant.lead_fields}


@router.put('/lead-config')
def set_config(body: LeadConfig, tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session.begin() as db:
        db.get(Tenant, tenant.id).lead_fields = body.fields
    return body


def lead_query(tenant_id, status, owner_id, source, from_date, to_date):
    query = select(Lead, Conversation.channel).join(
        Conversation, (Conversation.id == Lead.conversation_id) & (Conversation.tenant_id == Lead.tenant_id),
    ).where(Lead.tenant_id == tenant_id)
    if source:
        query = query.where(Conversation.channel == source)
    if from_date:
        query = query.where(Lead.created_at >= datetime.combine(from_date, time.min, timezone.utc))
    if to_date and to_date != date.max:
        query = query.where(Lead.created_at < datetime.combine(to_date + timedelta(days=1), time.min, timezone.utc))
    if status:
        query = query.where(Lead.status == status)
    if owner_id:
        query = query.where(Lead.owner_id == owner_id)
    return query.order_by(Lead.created_at.desc(), Lead.conversation_id)


def view(row, source=None):
    return {'id': row.conversation_id, 'conversation_id': row.conversation_id, 'fields': row.fields,
            'source': source, 'status': row.status, 'owner_id': row.owner_id, 'notes': row.notes,
            'created_at': row.created_at}


@router.get('/leads')
def list_leads(status: LeadStatus | None = None, owner_id: str | None = None,
               source: Literal['web', 'whatsapp'] | None = None,
               from_date: date | None = None, to_date: date | None = None,
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               tenant: Tenant = Depends(tenant_for_role('viewer'))):
    with Session() as db:
        query = lead_query(tenant.id, status, owner_id, source, from_date, to_date)
        return [view(row, channel) for row, channel in db.execute(query.offset(offset).limit(limit))]


def csv_cell(value):
    value = '' if value is None else str(value)
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


@router.get('/leads/export')
def export_leads(status: LeadStatus | None = None, owner_id: str | None = None,
                 source: Literal['web', 'whatsapp'] | None = None,
                 from_date: date | None = None, to_date: date | None = None,
                 tenant: Tenant = Depends(tenant_for_role('agent'))):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['conversation_id', *FIELDS, 'status', 'owner_id', 'notes', 'created_at', 'source'])
    with Session() as db:
        for row, channel in db.execute(lead_query(tenant.id, status, owner_id, source, from_date, to_date)):
            writer.writerow(map(csv_cell, [row.conversation_id, *(row.fields.get(key) for key in FIELDS),
                                           row.status, row.owner_id, row.notes, row.created_at.isoformat(), channel]))
    return Response(buffer.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="leads.csv"'})


@router.patch('/leads/{lead_id}')
def update_lead(lead_id: str, body: LeadUpdate, tenant: Tenant = Depends(tenant_for_role('agent'))):
    with Session.begin() as db:
        row = db.scalar(select(Lead).where(Lead.tenant_id == tenant.id, Lead.conversation_id == lead_id)
                        .with_for_update())
        if row is None:
            raise HTTPException(404, 'Lead not found')
        if body.owner_id is not None and not db.scalar(select(Membership.user_id).where(
            Membership.tenant_id == tenant.id, Membership.user_id == body.owner_id,
            Membership.role.in_(('owner', 'admin', 'agent')),
        )):
            raise HTTPException(422, 'Lead owner must be an agent in this workspace')
        if 'owner_id' in body.model_fields_set:
            row.owner_id = body.owner_id
        if body.status is not None:
            row.status = body.status
        if body.notes is not None:
            row.notes = body.notes
        return view(row)


@router.get('/lead-owners')
def lead_owners(tenant: Tenant = Depends(tenant_for_role('viewer'))):
    with Session() as db:
        rows = db.execute(select(User.id, User.name).join(Membership, Membership.user_id == User.id).where(
            Membership.tenant_id == tenant.id, Membership.role.in_(('owner', 'admin', 'agent')),
        ).order_by(User.name, User.id))
        return [{'id': user_id, 'name': name} for user_id, name in rows]


@router.get('/leads/{lead_id}')
def get_lead(lead_id: str, tenant: Tenant = Depends(tenant_for_role('viewer'))):
    with Session() as db:
        row = db.execute(lead_query(tenant.id, None, None, None, None, None)
                         .where(Lead.conversation_id == lead_id)).one_or_none()
        if row is None:
            raise HTTPException(404, 'Lead not found')
        return view(*row)
