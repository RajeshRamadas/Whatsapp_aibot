"""Owner controls for sharing one billing workspace with client workspaces."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select, text

from ..db import Membership, Plan, Session, Subscription, Tenant, UsageEvent, now
from ..services.audit import audit
from ..services.billing import TERMINAL
from ..services.usage import billing_events, month_start
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/agency', tags=['agency'])


class AgencyWorkspace(BaseModel):
    id: str
    name: str


class AgencyClient(AgencyWorkspace):
    ai_messages: int


class AgencyBilling(BaseModel):
    clients: list[AgencyClient]
    candidates: list[AgencyWorkspace]
    ai_messages: int
    ai_message_limit: int


def own(p: Principal):
    if p.user is None:
        raise HTTPException(403, 'A signed-in workspace owner is required')
    return p.user.id


def root(db, identity, *, lock=False):
    tenant = db.get(Tenant, identity, with_for_update=lock)
    if tenant is None or tenant.suspended or tenant.agency_id:
        raise HTTPException(409, 'Select the agency billing workspace')
    return tenant


@router.get('/billing', response_model=AgencyBilling)
def summary(p: Principal = Depends(require_role('owner'))):
    actor = own(p)
    with Session() as db:
        payer = root(db, p.tenant.id)
        counts = dict(db.execute(billing_events(payer.id, month_start(now()))
            .with_only_columns(UsageEvent.tenant_id, func.count()).group_by(UsageEvent.tenant_id)).all())
        clients = db.scalars(select(Tenant).where(Tenant.agency_id == payer.id).order_by(Tenant.name, Tenant.id)).all()
        sponsored = select(Tenant.agency_id).where(Tenant.agency_id.is_not(None))
        choices = db.scalars(select(Tenant).join(Membership, Membership.tenant_id == Tenant.id).where(
            Membership.user_id == actor, Membership.role == 'owner', Tenant.id != payer.id,
            Tenant.agency_id.is_(None), ~Tenant.id.in_(sponsored), Tenant.suspended.is_(False),
        ).order_by(Tenant.name, Tenant.id)).all()
        return AgencyBilling(clients=[AgencyClient(id=t.id, name=t.name, ai_messages=counts.get(t.id, 0))
            for t in clients], candidates=[AgencyWorkspace(id=t.id, name=t.name) for t in choices],
            ai_messages=sum(counts.values()), ai_message_limit=db.get(Plan, payer.plan_id).limits['ai_messages'])


def lock_links(db):
    # Keep simultaneous cross-links and nesting checks atomic. Usage and checkout lock the affected workspace rows.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('agency-client-links'))"))


@router.put('/clients/{client_id}', response_model=AgencyWorkspace)
def link_client(client_id: UUID, p: Principal = Depends(require_role('owner'))):
    actor = own(p)
    identity = str(client_id)
    if identity == p.tenant.id:
        raise HTTPException(422, 'Choose a different client workspace')
    with Session.begin() as db:
        lock_links(db)
        member = db.scalar(select(Membership).where(Membership.user_id == actor,
            Membership.tenant_id == identity, Membership.role == 'owner').with_for_update())
        if not member:
            raise HTTPException(404, 'Client workspace not found')
        client = db.get(Tenant, identity, with_for_update=True)
        if client is None or client.suspended:
            raise HTTPException(404, 'Client workspace not found')
        payer = root(db, p.tenant.id, lock=True)
        if client.agency_id == payer.id:
            return AgencyWorkspace(id=client.id, name=client.name)
        if client.agency_id or db.scalar(select(Tenant.id).where(Tenant.agency_id == client.id).limit(1)):
            raise HTTPException(409, 'Workspace already participates in another billing group')
        subscription = db.get(Subscription, client.id, with_for_update=True)
        if subscription and (subscription.status not in TERMINAL or
                            (subscription.period_end and subscription.period_end > now())):
            raise HTTPException(409, 'Finish the client workspace subscription before linking it')
        client.agency_id = payer.id
        audit(db, payer.id, 'agency.client_linked', client.id, actor_user_id=actor)
        audit(db, client.id, 'agency.billing_linked', payer.id, actor_user_id=actor)
        return AgencyWorkspace(id=client.id, name=client.name)


@router.delete('/clients/{client_id}', status_code=204)
def unlink_client(client_id: UUID, p: Principal = Depends(require_role('owner'))):
    actor = own(p)
    with Session.begin() as db:
        lock_links(db)
        client = db.scalar(select(Tenant).where(Tenant.id == str(client_id),
            Tenant.agency_id == p.tenant.id).with_for_update())
        if not client:
            raise HTTPException(404, 'Client workspace not found')
        payer = root(db, p.tenant.id, lock=True)
        client.agency_id = None
        audit(db, payer.id, 'agency.client_unlinked', client.id, actor_user_id=actor)
        audit(db, client.id, 'agency.billing_unlinked', payer.id, actor_user_id=actor)
    return Response(status_code=204)
