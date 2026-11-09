"""Workspace channel configuration and connection status."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select

from ..config import settings
from ..db import Channel, Session, Tenant
from ..services.crypto import encrypt
from .principal import tenant_for_role

router = APIRouter(prefix='/api/v1/channels')


class ChannelUpdate(BaseModel):
    access_token: SecretStr | None = None
    display_name: str | None = Field(None, max_length=200)


def view(channel):
    has_token = channel.credentials_enc is not None
    configured = settings.whatsapp_enabled and (has_token or bool(settings.whatsapp_access_token))
    return {'id': channel.id, 'kind': channel.kind, 'external_id': channel.external_id,
            'display_name': channel.display_name, 'last_webhook_at': channel.last_webhook_at,
            'campaign_quality': channel.campaign_quality, 'campaign_verified_limit': channel.campaign_limit,
            'campaign_checked_at': channel.campaign_checked_at,
            'campaign_portfolio': channel.campaign_portfolio,
            'status': 'configured' if configured else 'unconfigured', 'has_channel_token': has_token}


def scoped(db, tenant_id, channel_id):
    channel = db.scalar(select(Channel).where(Channel.id == channel_id, Channel.tenant_id == tenant_id))
    if channel is None:
        raise HTTPException(404, 'Channel not found')
    return channel


@router.get('')
def list_channels(tenant: Tenant = Depends(tenant_for_role('viewer'))):
    with Session() as db:
        return [view(channel) for channel in db.scalars(select(Channel).where(Channel.tenant_id == tenant.id)
                                                        .order_by(Channel.kind, Channel.id))]


@router.get('/{channel_id}')
def channel_status(channel_id: str, tenant: Tenant = Depends(tenant_for_role('viewer'))):
    with Session() as db:
        return view(scoped(db, tenant.id, channel_id))


@router.patch('/{channel_id}')
def update_channel(channel_id: str, body: ChannelUpdate, tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session() as db:
        scoped(db, tenant.id, channel_id)
    encrypted = None
    if body.access_token is not None:
        token = body.access_token.get_secret_value()
        if not 1 <= len(token) <= 8192 or any(char.isspace() for char in token):
            raise HTTPException(422, 'Invalid access token')
        try:
            encrypted = encrypt(tenant.id, token)
        except ValueError as exc:
            raise HTTPException(503, 'Channel credential encryption is unavailable') from exc
    with Session.begin() as db:
        db.get(Tenant, tenant.id, with_for_update=True)
        channel = scoped(db, tenant.id, channel_id)
        if 'display_name' in body.model_fields_set:
            channel.display_name = body.display_name
        if 'access_token' in body.model_fields_set:
            channel.credentials_enc = encrypted
        return view(channel)
