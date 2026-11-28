"""Owner-managed appearance and public branding for verified agency domains."""
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import Session, now
from ..models.branding import AgencyBrand
from ..services import branding
from ..services.audit import audit
from ..services.auth import new_token
from .agency import own, root
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1', tags=['agency branding'])


class BrandAppearance(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    display_name: str = Field(min_length=1, max_length=120)
    logo_url: str | None = Field(None, max_length=500)
    primary_color: str = Field('#155e75', pattern=r'^#[0-9a-fA-F]{6}$')
    secondary_color: str = Field('#edf3f7', pattern=r'^#[0-9a-fA-F]{6}$')

    @field_validator('logo_url')
    @classmethod
    def logo(cls, value):
        if value is None:
            return None
        url = urlsplit(value)
        if url.scheme != 'https' or url.username or url.password or not branding.domain_name(url.hostname or ''):
            raise ValueError('Use a public HTTPS logo URL')
        return value


class BrandSettings(BrandAppearance):
    domain: str | None = Field(None, max_length=253)

    @field_validator('domain')
    @classmethod
    def domain_host(cls, value):
        if value is None:
            return None
        host = branding.domain_name(value)
        if not host or host == urlsplit(settings.public_app_url).hostname:
            raise ValueError('Use your own domain name without a scheme, port or path')
        return host


class BrandView(BrandSettings):
    verified_at: datetime | None = None
    dns_name: str | None = None
    dns_value: str | None = None


def view(brand, fallback):
    if not brand:
        return BrandView(display_name=fallback[:120])
    return BrandView(display_name=brand.display_name, logo_url=brand.logo_url, primary_color=brand.primary_color,
        secondary_color=brand.secondary_color, domain=brand.domain, verified_at=brand.verified_at,
        dns_name=branding.challenge_name(brand.domain) if brand.domain else None, dns_value=brand.challenge)


@router.get('/agency/branding', response_model=BrandView)
def get_brand(p: Principal = Depends(require_role('owner'))):
    own(p)
    with Session() as db:
        tenant = root(db, p.tenant.id)
        return view(db.get(AgencyBrand, tenant.id), tenant.name)


@router.put('/agency/branding', response_model=BrandView)
def change_brand(body: BrandSettings, p: Principal = Depends(require_role('owner'))):
    actor = own(p)
    with Session.begin() as db:
        tenant = root(db, p.tenant.id, lock=True)
        brand = db.get(AgencyBrand, tenant.id, with_for_update=True)
        if not brand:
            brand = AgencyBrand(tenant_id=tenant.id)
            db.add(brand)
        if brand.domain != body.domain:
            brand.challenge = new_token() if body.domain else None
            brand.verified_at = None
        for key, value in body.model_dump().items():
            setattr(brand, key, value)
        audit(db, tenant.id, 'agency.branding_updated', tenant.id, actor_user_id=actor)
        return view(brand, tenant.name)


@router.post('/agency/branding/verify', response_model=BrandView)
def verify_brand(p: Principal = Depends(require_role('owner'))):
    actor = own(p)
    with Session() as db:
        tenant = root(db, p.tenant.id)
        before = db.get(AgencyBrand, tenant.id)
        if not before or not before.domain or not before.challenge:
            raise HTTPException(409, 'Save a domain before verifying it')
        domain, challenge = before.domain, before.challenge
    if not branding.proves_ownership(domain, challenge):
        raise HTTPException(422, 'The expected DNS TXT record was not found')
    try:
        with Session.begin() as db:
            tenant = root(db, p.tenant.id, lock=True)
            brand = db.get(AgencyBrand, tenant.id, with_for_update=True)
            if not brand or (brand.domain, brand.challenge) != (domain, challenge):
                raise HTTPException(409, 'Domain settings changed during verification')
            brand.verified_at = now()
            audit(db, tenant.id, 'agency.domain_verified', tenant.id, actor_user_id=actor)
            return view(brand, tenant.name)
    except IntegrityError:
        raise HTTPException(409, 'This domain is already verified for another agency') from None


@router.get('/branding', response_model=BrandAppearance | None)
def public_brand(response: Response, domain: str = Query(max_length=253)):
    response.headers['Cache-Control'] = 'no-store'
    with Session() as db:
        brand = branding.verified(db, domain)
        return BrandAppearance.model_validate(brand, from_attributes=True) if brand else None


@router.get('/branding/domain', status_code=204)
def certificate_domain(request: Request, domain: str | None = Query(None, max_length=253)):
    host = domain or request.headers.get(branding.FORWARDED_HOST)
    with Session() as db:
        if not branding.verified(db, host):
            raise HTTPException(404, 'Domain unavailable')
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})
