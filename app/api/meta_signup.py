"""Connect a workspace through Meta Embedded Signup."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, SecretStr, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import Session, Tenant
from ..rate_limits import RateLimiter
from ..services.channels import access_token, find_whatsapp, sync_whatsapp, whatsapp_channel
from ..services.crypto import encrypt
from ..services.meta_signup import MetaSignup, MetaSignupError
from .channels import view
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/whatsapp/signup')
test_limiter = RateLimiter(3, namespace='whatsapp-signup-test')
GraphId = Annotated[str, StringConstraints(pattern=r'^[0-9]{1,32}$')]


class SignupInput(BaseModel):
    code: SecretStr
    waba_id: GraphId
    phone_number_id: GraphId
    pin: Annotated[str, StringConstraints(pattern=r'^[0-9]{6}$')]


class TestInput(BaseModel):
    recipient: Annotated[str, StringConstraints(pattern=r'^\+?[1-9][0-9]{6,14}$')]


def admin_session(principal: Principal = Depends(require_role('admin'))):
    if principal.user is None:
        raise HTTPException(403, 'A signed-in workspace admin is required')
    return principal


def available():
    return bool(settings.whatsapp_app_id and settings.whatsapp_signup_config_id
                and settings.whatsapp_enabled and settings.master_key)


def check_phone(db, tenant, phone_id):
    if tenant.whatsapp_phone_number_id and tenant.whatsapp_phone_number_id != phone_id:
        raise HTTPException(409, 'Unlink the current WhatsApp number before connecting a different one')
    channel = find_whatsapp(db, phone_id)
    owner = db.scalar(select(Tenant.id).where(Tenant.whatsapp_phone_number_id == phone_id))
    if (channel and channel.tenant_id != tenant.id) or (owner and owner != tenant.id):
        raise HTTPException(409, 'This WhatsApp number is already linked to another workspace')


@router.get('/config')
def signup_config(principal: Principal = Depends(admin_session)):
    with Session() as db:
        channel = whatsapp_channel(db, principal.tenant.id)
        return {'enabled': available(), 'app_id': settings.whatsapp_app_id if available() else None,
                'config_id': settings.whatsapp_signup_config_id if available() else None,
                'version': settings.whatsapp_graph_version, 'channel': view(channel) if channel else None}


@router.post('')
def complete_signup(body: SignupInput, principal: Principal = Depends(admin_session)):
    if not available():
        raise HTTPException(503, 'Embedded Signup has not been configured on this server')
    code = body.code.get_secret_value()
    if not 1 <= len(code) <= 8192:
        raise HTTPException(422, 'Invalid Meta authorization code')
    tenant_id = principal.tenant.id
    with Session() as db:
        check_phone(db, db.get(Tenant, tenant_id), body.phone_number_id)
    graph = MetaSignup()
    try:
        token = graph.exchange(code)
        name = graph.verify_phone(body.waba_id, body.phone_number_id, token)
        credentials = encrypt(tenant_id, token)
        graph.subscribe(body.waba_id, token)
        graph.register(body.phone_number_id, body.pin, token)
    except MetaSignupError as exc:
        raise HTTPException(502, str(exc)) from None
    except ValueError:
        raise HTTPException(503, 'Channel credential encryption is unavailable') from None
    try:
        with Session.begin() as db:
            tenant = db.get(Tenant, tenant_id, with_for_update=True)
            check_phone(db, tenant, body.phone_number_id)
            tenant.whatsapp_phone_number_id = body.phone_number_id
            sync_whatsapp(db, tenant)
            db.flush()
            channel = whatsapp_channel(db, tenant_id)
            channel.credentials_enc = credentials
            channel.display_name = name
            return view(channel)
    except IntegrityError:
        raise HTTPException(409, 'This WhatsApp number is already linked') from None


@router.post('/test-message')
def test_message(body: TestInput, principal: Principal = Depends(admin_session)):
    tenant_id = principal.tenant.id
    with Session() as db:
        channel = whatsapp_channel(db, tenant_id)
        if channel is None:
            raise HTTPException(409, 'Connect a WhatsApp number first')
        phone_id = channel.external_id
    if not test_limiter.allow(tenant_id):
        raise HTTPException(429, 'Wait a minute before sending another test message')
    try:
        token = access_token(phone_id, tenant_id)
        MetaSignup().send_test(phone_id, body.recipient.lstrip('+'), token)
    except MetaSignupError as exc:
        raise HTTPException(502, str(exc)) from None
    except (RuntimeError, ValueError):
        raise HTTPException(503, 'WhatsApp credentials are unavailable') from None
    return {'status': 'accepted'}
