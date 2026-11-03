"""Resolve WhatsApp channels and their tenant-scoped credentials."""
from sqlalchemy import select

from ..config import settings
from ..db import Channel, Session, Tenant
from .crypto import decrypt


def whatsapp_channel(db, tenant_id: str) -> Channel | None:
    return db.scalar(select(Channel).where(Channel.tenant_id == tenant_id, Channel.kind == 'whatsapp'))


def find_whatsapp(db, phone_id: str) -> Channel | None:
    return db.scalar(select(Channel).where(Channel.kind == 'whatsapp', Channel.external_id == phone_id))


def tenant_for_phone(db, phone_id: str, channel: Channel | None) -> str | None:
    if channel is not None:
        return channel.tenant_id
    # Older fixtures and direct integrations can still set the mirrored tenant field.
    return db.scalar(select(Tenant.id).where(Tenant.whatsapp_phone_number_id == phone_id))


def access_token(phone_id: str, tenant_id: str | None = None) -> str:
    with Session() as db:
        channel = find_whatsapp(db, phone_id)
        owner = tenant_for_phone(db, phone_id, channel)
    if tenant_id is not None and owner != tenant_id:
        raise RuntimeError('WhatsApp channel is no longer linked to this workspace')
    if channel is not None and channel.credentials_enc is not None:
        return decrypt(channel.tenant_id, channel.credentials_enc).decode()
    if not settings.whatsapp_access_token:
        raise RuntimeError('WhatsApp channel has no access token')
    return settings.whatsapp_access_token


def sync_whatsapp(db, tenant: Tenant) -> None:
    channel = whatsapp_channel(db, tenant.id)
    phone_id = tenant.whatsapp_phone_number_id
    if channel is not None and channel.external_id != phone_id:
        db.delete(channel)
        db.flush()
        channel = None
    if phone_id and channel is None:
        db.add(Channel(tenant_id=tenant.id, kind='whatsapp', external_id=phone_id))
