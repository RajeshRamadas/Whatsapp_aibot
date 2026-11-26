"""Admin-only verified Meta Page/Instagram connection lifecycle."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, SecretStr, StringConstraints
from sqlalchemy import select, update

from ..db import Channel, Conversation, Message, MetaDmEvent, Session, Tenant
from ..services import meta_connections as service
from ..services.events import publish_sync
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
            Message.role == 'agent', Message.delivery_status == 'queued').values(
                delivery_status='failed', error='Meta channel disconnected before delivery'))
        db.execute(update(MetaDmEvent).where(MetaDmEvent.tenant_id == principal.tenant.id,
            MetaDmEvent.channel_id == channel.id, MetaDmEvent.status == 'queued').values(status='ignored'))
        db.delete(channel)
    for identity in conversations:
        publish_sync(principal.tenant.id, 'message.updated', {'conversation_id': identity})
        publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': identity})
