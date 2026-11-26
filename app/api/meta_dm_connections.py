"""Admin-only verified Meta Page/Instagram connection lifecycle."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, SecretStr, StrictBool, StringConstraints
from sqlalchemy import select, update

from ..db import Channel, Conversation, Message, MetaDmEvent, Session, Tenant
from ..services import meta_connections as service
from ..services.events import publish_sync
from ..services.meta_bot import cancel as cancel_meta_bot
from ..services.meta_signup import MetaSignup
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


@router.post('/channels', status_code=201)
def connect(body: Connect, principal: Principal = Depends(admin)):
    try:
        channel = service.connect(service.ConnectionRequest(body.kind, body.external_id, body.access_token),
                                  principal.tenant.id, graph=MetaSignup())
        return view(channel)
    except service.ConnectionError as exc:
        raise HTTPException(exc.status, str(exc)) from None


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
        if db.scalar(select(Message.id).where(Message.conversation_id.in_(conversations),
                                             Message.delivery_status == 'sending').limit(1)):
            raise HTTPException(409, 'A reply is being sent. Wait for it to finish before disconnecting')
        db.execute(update(Message).where(Message.conversation_id.in_(conversations),
            Message.role.in_(('agent', 'assistant')), Message.delivery_status == 'queued').values(
                delivery_status='failed', error='Meta channel disconnected before delivery'))
        db.execute(update(MetaDmEvent).where(MetaDmEvent.tenant_id == principal.tenant.id,
            MetaDmEvent.channel_id == channel.id, MetaDmEvent.status == 'queued').values(status='ignored'))
        db.delete(channel)
    for identity in conversations:
        publish_sync(principal.tenant.id, 'message.updated', {'conversation_id': identity})
        publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': identity})


class MetaBotChange(BaseModel):
    model_config = {'extra': 'forbid'}
    enabled: StrictBool


class MetaBotSetting(BaseModel):
    channel_id: str
    enabled: bool


@router.put('/channels/{channel_id}/bot', response_model=MetaBotSetting)
def set_bot(channel_id: UUID, body: MetaBotChange, principal: Principal = Depends(admin)):
    from ..models.meta_bot import MetaBotTurn
    from ..services.audit import audit
    with Session.begin() as db:
        db.get(Tenant, principal.tenant.id, with_for_update=True)
        channel = db.scalar(select(Channel).where(Channel.id == str(channel_id),
            Channel.tenant_id == principal.tenant.id, Channel.kind.in_(('instagram', 'messenger'))).with_for_update())
        if not channel:
            raise HTTPException(404, 'Meta channel not found')
        if body.enabled and not channel.credentials_enc:
            raise HTTPException(409, 'Connect the account before enabling bot replies')
        conversations = db.scalars(select(Conversation).where(Conversation.tenant_id == principal.tenant.id,
            Conversation.meta_channel_id == channel.id).with_for_update()).all()
        identities = [conv.id for conv in conversations]
        if not body.enabled:
            if db.scalar(select(Message.id).where(Message.conversation_id.in_(identities),
                    Message.role == 'assistant', Message.delivery_status == 'sending').limit(1)):
                raise HTTPException(409, 'A bot reply is being sent; wait for it to finish')
            db.execute(update(MetaBotTurn).where(MetaBotTurn.tenant_id == principal.tenant.id,
                MetaBotTurn.channel_id == channel.id, MetaBotTurn.status.in_(('queued', 'processing')))
                .values(status='cancelled', claim_token=None))
            for conv in conversations:
                cancel_meta_bot(db, conv)
                if conv.status == 'bot':
                    conv.status, conv.handoff = 'waiting', True
        channel.meta_bot_enabled = body.enabled
        audit(db, principal.tenant.id, 'meta.bot_changed', channel.id, {'enabled': body.enabled},
              principal.user.id, 'user')
    for identity in identities:
        publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': identity})
        publish_sync(principal.tenant.id, 'message.updated', {'conversation_id': identity})
    return {'channel_id': str(channel_id), 'enabled': body.enabled}
