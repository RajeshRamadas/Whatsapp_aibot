"""Workspace-admin website knowledge controls. Fetches run exclusively in workers."""
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from ..db import Session, Source, Tenant, Website, now
from ..services.audit import audit
from ..services.billing import entitled
from ..services.web_fetch import canonical
from ..services.website_crawl import budget, reserved
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/websites', tags=['websites'])
Schedule = Literal['manual', 'daily', 'weekly']


class Cadence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schedule: Schedule


class WebsiteInput(Cadence):
    url: str = Field(min_length=1, max_length=2048)
    max_pages: int = Field(1, ge=1, le=50, strict=True)
    depth: int = Field(0, ge=0, le=3, strict=True)

    @field_validator('url')
    @classmethod
    def public_url(cls, value):
        return canonical(value)


class WebsiteView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    url: str
    max_pages: int
    depth: int
    schedule: Schedule
    status: Literal['queued', 'running', 'ready', 'failed']
    error: str | None
    next_run: datetime | None
    last_success: datetime | None
    pages: int = 0


class WebsiteList(BaseModel):
    items: list[WebsiteView]
    total: int
    limit: int
    offset: int
    page_limit: int
    reserved_pages: int


class PageView(BaseModel):
    id: str
    url: str


def scoped(db, identity, tenant_id):
    site = db.scalar(select(Website).where(Website.id == str(identity), Website.tenant_id == tenant_id)
                     .with_for_update())
    if site is None:
        raise HTTPException(404, 'Website not found')
    return site


def account(db, tenant_id):
    return db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())


def permitted(db, tenant, additional=0):
    if not entitled(db, tenant, now()) or reserved(db, tenant.id) + additional > budget(db, tenant):
        raise HTTPException(409, 'Website page budget exceeded. Remove a website or upgrade your plan.')


def record(db, principal, action, site):
    audit(db, principal.tenant.id, action, site.id,
          actor_user_id=principal.user.id if principal.user else None,
          actor_type='user' if principal.user else 'api_key')


@router.get('', response_model=WebsiteList)
def websites(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
             principal: Principal = Depends(require_role('admin'))):
    tenant = principal.tenant
    with Session() as db:
        rows = db.scalars(select(Website).where(Website.tenant_id == tenant.id)
                          .order_by(Website.created_at, Website.id).offset(offset).limit(limit)).all()
        counts = dict(db.execute(select(Source.website_id, func.count()).where(Source.tenant_id == tenant.id,
            Source.website_id.in_([row.id for row in rows])).group_by(Source.website_id)).all())
        return WebsiteList(items=[WebsiteView.model_validate(row).model_copy(update={'pages': counts.get(row.id, 0)})
                                  for row in rows], total=db.scalar(select(func.count()).select_from(Website)
                                  .where(Website.tenant_id == tenant.id)), limit=limit, offset=offset,
                           page_limit=budget(db, tenant), reserved_pages=reserved(db, tenant.id))


@router.post('', response_model=WebsiteView, status_code=202)
def create(body: WebsiteInput, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        tenant = account(db, principal.tenant.id)
        permitted(db, tenant, body.max_pages)
        if db.scalar(select(Website.id).where(Website.tenant_id == tenant.id, Website.url == body.url)):
            raise HTTPException(409, 'This website is already connected.')
        site = Website(tenant_id=tenant.id, **body.model_dump())
        db.add(site)
        db.flush()
        record(db, principal, 'website.created', site)
        return WebsiteView.model_validate(site)


@router.post('/{website_id}/refresh', response_model=WebsiteView, status_code=202)
def refresh(website_id: UUID, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        tenant = account(db, principal.tenant.id)
        site = scoped(db, website_id, tenant.id)
        permitted(db, tenant)
        if site.status == 'running':
            raise HTTPException(409, 'This website is still being refreshed.')
        if site.status != 'queued':
            site.status, site.error, site.attempts, site.next_run = 'queued', None, 0, now()
            record(db, principal, 'website.refreshed', site)
        return WebsiteView.model_validate(site)


@router.patch('/{website_id}', response_model=WebsiteView)
def schedule(website_id: UUID, body: Cadence, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        account(db, principal.tenant.id)
        site = scoped(db, website_id, principal.tenant.id)
        site.schedule = body.schedule
        if site.status not in ('running', 'queued'):
            days = {'daily': 1, 'weekly': 7}.get(body.schedule)
            site.next_run = now() + timedelta(days=days) if days else None
        record(db, principal, 'website.scheduled', site)
        return WebsiteView.model_validate(site)


@router.delete('/{website_id}', status_code=204)
def remove(website_id: UUID, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        account(db, principal.tenant.id)
        site = scoped(db, website_id, principal.tenant.id)
        record(db, principal, 'website.deleted', site)
        db.delete(site)  # Pages and chunks cascade; an in-flight run loses its publication token.


@router.get('/{website_id}/pages', response_model=list[PageView])
def pages(website_id: UUID, principal: Principal = Depends(require_role('admin'))):
    with Session() as db:
        scoped(db, website_id, principal.tenant.id)
        return [PageView(id=row.id, url=row.url) for row in db.scalars(select(Source).where(
            Source.tenant_id == principal.tenant.id, Source.website_id == str(website_id)).order_by(Source.url))]
