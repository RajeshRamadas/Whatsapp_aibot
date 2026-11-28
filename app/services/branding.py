"""Resolve previously verified hosts and check a DNS ownership challenge."""
import re
from urllib.parse import urlsplit

from dns import exception, resolver
from sqlalchemy import select

from ..config import settings
from ..db import Tenant
from ..models.branding import AgencyBrand

LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
FORWARDED_HOST = 'X-Agency-Host'


def domain_name(value: str) -> str | None:
    try:
        name = value.strip().rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError:
        return None
    parts = name.split('.')
    if (len(name) > 253 or len(parts) < 2 or not parts[-1][0:1].isalpha()
            or not all(LABEL.fullmatch(part) for part in parts)):
        return None
    return name


def verified(db, host):
    host = domain_name(host or '')
    if not host or host == urlsplit(settings.public_app_url).hostname:
        return None
    return db.scalar(select(AgencyBrand).join(Tenant, Tenant.id == AgencyBrand.tenant_id).where(
        AgencyBrand.domain == host, AgencyBrand.verified_at.is_not(None),
        Tenant.suspended.is_(False), Tenant.agency_id.is_(None)))


def request_brand(db, request):
    # The web proxy overwrites this header. On a direct API request the actual
    # verified Host always takes precedence over the forwarded context.
    host = request.headers.get('host', '').partition(':')[0]
    forwarded = request.headers.get(FORWARDED_HOST, '').partition(':')[0]
    return verified(db, host) or verified(db, forwarded)


def allows(brand, tenant):
    return not brand or brand.tenant_id in (tenant.id, tenant.agency_id)


def challenge_name(domain):
    return '_chatbot-verification.' + domain


def proves_ownership(domain, token):
    try:
        records = resolver.resolve(challenge_name(domain), 'TXT', lifetime=4, search=False)
        return any(b''.join(record.strings) == token.encode('ascii') for record in records)
    except exception.DNSException:
        return False
