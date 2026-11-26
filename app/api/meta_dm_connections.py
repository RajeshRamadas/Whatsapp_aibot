"""Admin-only verified Meta Page/Instagram connection lifecycle."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, SecretStr, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import Channel, Conversation, Message, MetaDmEvent, Session, Tenant
from ..services.crypto import encrypt
from ..services.events import publish_sync
from ..services.meta_signup import MetaSignup, MetaSignupError
from .channels import view
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/meta-dm')
GraphId = Annotated[str, StringConstraints(pattern=r'^[0-9]{1,32}$')]


class Connect(BaseModel):
    kind: Annotated[str, StringConstraints(pattern=r'^(instagram|messenger)$')]
    external_id: GraphId
    access_token: SecretStr


def admin(principal: Principal = Depends(require_role('admin'))):
    if principal.user is None:
        raise HTTPException(403, 'A signed-in workspace admin is required')
    return principal


def verify_connection(kind: str, external_id: str, token: str) -> str | None:
    """Verify the supplied token can access the claimed Meta asset before linking it."""
    graph = MetaSignup()
    result = graph.request(external_id, params={'fields': 'id,name'}, token=token)
    if str(result.get('id')) != external_id:
        raise MetaSignupError('Meta did not verify ownership of the selected asset')
    # A successful subscription proves the token can manage webhooks for the asset.
    result = graph.request(f'{external_id}/subscribed_apps', params={'subscribed_fields': 'messages'},
                           token=token, payload={})
    if result.get('success') is not True:
        raise MetaSignupError('Meta did not confirm messaging webhook subscription')
    return str(result.get('name') or '')[:200] or None


@router.post('/channels', status_code=201)
def connect(body: Connect, principal: Principal = Depends(admin)):
    if not settings.whatsapp_app_secret or not settings.whatsapp_verify_token:
        raise HTTPException(503, 'Meta webhook verification is not configured')
    token = body.access_token.get_secret_value()
    if not 1 <= len(token) <= 8192 or any(char.isspace() for char in token):
        raise HTTPException(422, 'Invalid Meta access token')
    with Session() as db:
        existing = db.scalar(select(Channel).where(Channel.kind == body.kind,
                                                   Channel.external_id == body.external_id))
        if existing is not None:
            raise HTTPException(409, 'This Meta asset is already connected')
        if db.scalar(select(Channel.id).where(Channel.tenant_id == principal.tenant.id,
                                              Channel.kind == body.kind)):
            raise HTTPException(409, 'Disconnect the current channel before connecting another')
    try:
        name = verify_connection(body.kind, body.external_id, token)
        encrypted = encrypt(principal.tenant.id, token)
    except MetaSignupError:
        raise HTTPException(502, 'Meta could not verify and subscribe this asset') from None
    except ValueError:
        raise HTTPException(503, 'Credential encryption is unavailable') from None
    try:
        with Session.begin() as db:
            db.get(Tenant, principal.tenant.id, with_for_update=True)
            if db.scalar(select(Channel.id).where(Channel.tenant_id == principal.tenant.id,
                                                  Channel.kind == body.kind)):
                raise HTTPException(409, 'A channel of this kind is already connected')
            channel = Channel(tenant_id=principal.tenant.id, kind=body.kind, external_id=body.external_id,
                              display_name=name, credentials_enc=encrypted)
            db.add(channel)
            db.flush()
            return view(channel)
    except IntegrityError:
        raise HTTPException(409, 'This Meta asset is already connected') from None


@router.delete('/channels/{channel_id}', status_code=204)
def disconnect(channel_id: str, principal: Principal = Depends(admin)):
    with Session.begin() as db:
        db.get(Tenant, principal.tenant.id, with_for_update=True)
        channel = db.scalar(select(Channel).where(Channel.id == channel_id,
                                                   Channel.tenant_id == principal.tenant.id,
                                                   Channel.kind.in_(('instagram', 'messenger'))))
        if channel is None:
            raise HTTPException(404, 'Meta channel not found')
        conversations = db.scalars(select(Conversation.id).where(
            Conversation.tenant_id == principal.tenant.id, Conversation.meta_channel_id == channel.id)).all()
        db.execute(update(Message).where(Message.conversation_id.in_(conversations),
            Message.role == 'agent', Message.delivery_status == 'queued').values(
                delivery_status='failed', error='Meta channel disconnected before delivery'))
        db.execute(update(MetaDmEvent).where(MetaDmEvent.tenant_id == principal.tenant.id,
            MetaDmEvent.channel_id == channel.id, MetaDmEvent.status == 'queued').values(status='ignored'))
        db.delete(channel)
    for identity in conversations:
        publish_sync(principal.tenant.id, 'message.updated', {'conversation_id': identity})
        publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': identity})
