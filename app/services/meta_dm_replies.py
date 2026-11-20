"""Send tenant-scoped Meta DM agent replies with a conservative 24-hour window."""
from datetime import timedelta

from sqlalchemy import select, update

from ..db import Channel, Conversation, Message, Session, Tenant, now
from .crypto import decrypt
from .events import publish_sync
from .meta_signup import MetaSignup, MetaSignupError


def send(channel: Channel, recipient: str, content: str) -> str:
    token = decrypt(channel.tenant_id, channel.credentials_enc).decode()
    # Messenger Send API and Instagram Messaging API both accept this payload.
    result = MetaSignup().request(f'{channel.external_id}/messages', token=token, payload={
        'recipient': {'id': recipient}, 'messaging_type': 'RESPONSE',
        'message': {'text': content},
    })
    mid = result.get('message_id')
    if not isinstance(mid, str) or not mid:
        raise MetaSignupError('Meta did not confirm the message')
    return mid


def dispatch_one() -> bool:
    with Session.begin() as db:
        row = db.execute(select(Message, Conversation, Tenant)
                         .join(Conversation, Conversation.id == Message.conversation_id)
                         .join(Tenant, Tenant.id == Conversation.tenant_id)
                         .where(Message.role == 'agent', Message.delivery_status == 'queued',
                                Conversation.channel.in_(('instagram', 'messenger')))
                         .order_by(Message.id).limit(1)
                         .with_for_update(skip_locked=True, of=Message)).first()
        if row is None:
            return False
        message, conv, tenant = row
        channel = db.scalar(select(Channel).where(Channel.tenant_id == tenant.id,
                                                   Channel.kind == conv.channel))
        eligible = bool(not tenant.suspended and conv.status == 'agent' and conv.handoff
                        and conv.external_user and channel and channel.credentials_enc
                        and conv.last_inbound_at
                        and conv.last_inbound_at > now() - timedelta(hours=24))
        message_id, conversation_id, tenant_id = message.id, conv.id, tenant.id
        recipient, content = conv.external_user, message.content
        if eligible:
            message.delivery_status = 'sending'
            message.dispatch_started_at = now()
            channel_id = channel.id
        else:
            message.delivery_status = 'failed'
            message.error = 'Meta DM reply is no longer eligible or the messaging window expired'
    if eligible:
        provider_id = None
        try:
            with Session() as db:
                current = db.get(Channel, channel_id)
                if current is not None and current.tenant_id == tenant_id and current.credentials_enc:
                    provider_id = send(current, recipient, content)
        except (MetaSignupError, ValueError, RuntimeError):
            pass
        with Session.begin() as db:
            message = db.get(Message, message_id)
            if message:
                message.channel_message_id = provider_id
                message.delivery_status = 'sent' if provider_id else 'unknown'
                if not provider_id:
                    message.error = 'Meta delivery could not be confirmed; do not automatically retry'
    publish_sync(tenant_id, 'message.updated', {'conversation_id': conversation_id})
    return True


def expire_stale() -> None:
    with Session.begin() as db:
        db.execute(update(Message).where(
            Message.id.in_(select(Message.id).join(Conversation)
                           .where(Conversation.channel.in_(('instagram', 'messenger')))),
            Message.role == 'agent', Message.delivery_status == 'sending',
            Message.dispatch_started_at < now() - timedelta(minutes=2),
        ).values(delivery_status='unknown', error='Meta delivery could not be confirmed'))
