"""Ordered, asset-bound Meta replies; every provider call runs without a Session."""
from datetime import timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.orm import aliased

from ..db import Channel, Conversation, Message, Session, Tenant, now
from . import meta_outbound_media
from .bot_policy import within_hours
from .crypto import decrypt
from .events import publish_sync
from .meta_signup import MetaSignup, MetaSignupError


def send(channel: Channel, recipient: str, content: str) -> str:
    token = decrypt(channel.tenant_id, channel.credentials_enc).decode()
    result = MetaSignup().request(f'{channel.external_id}/messages', token=token, payload={
        'recipient': {'id': recipient}, 'messaging_type': 'RESPONSE', 'message': {'text': content}})
    mid = result.get('message_id')
    if not isinstance(mid, str) or not mid:
        raise MetaSignupError('Meta did not confirm the message')
    return mid


def eligible(message, conv, tenant, channel):
    limit = 1000 if conv.channel == 'instagram' else 2000
    human = message.role == 'agent' and conv.status == 'agent' and conv.handoff
    notice = bool(message.reply_key and message.reply_key.startswith('meta-notice:')
                  and conv.status == 'waiting' and conv.handoff)
    bot = (message.role == 'assistant' and tenant.bot_live and channel and channel.meta_bot_enabled
           and (notice or (conv.status == 'bot' and not conv.handoff and within_hours(tenant))))
    return bool(not tenant.suspended and (human or bot) and conv.external_user
                and channel and channel.id == conv.meta_channel_id and channel.tenant_id == tenant.id
                and channel.kind == conv.channel and channel.credentials_enc and conv.last_inbound_at
                and now() - timedelta(hours=24) < conv.last_inbound_at <= now()
                and len(message.content.encode('utf-8')) <= limit)


def dispatch_one() -> bool:
    other = aliased(Message)
    with Session.begin() as db:
        row = db.execute(select(Message, Conversation, Tenant)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .join(Tenant, Tenant.id == Conversation.tenant_id)
            .where(Message.role.in_(('agent', 'assistant')), Message.kind.in_(('text', 'image', 'document', 'audio')),
                   Message.delivery_status == 'queued',
                   Conversation.channel.in_(('instagram', 'messenger')),
                   ~exists(select(other.id).where(other.conversation_id == Conversation.id,
                                                 other.role.in_(('agent', 'assistant')),
                                                 other.delivery_status == 'sending')))
            .order_by(Message.id).limit(1)
            .with_for_update(skip_locked=True, of=(Message, Conversation, Tenant))).first()
        if row is None:
            return False
        message, conv, tenant = row
        # The locking SELECT's snapshot can predate a competing claim's commit.
        # Tenant/conversation locks are now held; use a fresh statement before claiming.
        if db.scalar(select(other.id).where(other.conversation_id == conv.id,
            other.id != message.id, other.role.in_(('agent', 'assistant')),
            (other.delivery_status == 'sending') |
            ((other.delivery_status == 'queued') & (other.id < message.id))).limit(1)) is not None:
            return False
        message_id, conversation_id, tenant_id = message.id, conv.id, tenant.id
        message.delivery_status, message.dispatch_started_at = 'sending', now()
    # A later dispatcher sees 'sending' and cannot overtake this conversation.
    attempted, provider_id, attachment = False, None, None
    try:
        with Session() as db:
            message = db.get(Message, message_id)
            conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                                                        Conversation.tenant_id == tenant_id))
            tenant = db.get(Tenant, tenant_id)
            channel = db.scalar(select(Channel).where(Channel.id == conv.meta_channel_id,
                                                       Channel.tenant_id == tenant_id)) if conv else None
            ready = bool(message and conv and tenant and message.delivery_status == 'sending'
                         and eligible(message, conv, tenant, channel))
            recipient, content = (conv.external_user, message.content) if ready else (None, None)
            attachment = dict(message.attachments[0]) if message and message.attachments else None
            data = message.upload_data if ready and attachment else None
        if ready and bool(attachment) != (message.kind != 'text'):
            raise ValueError('Invalid Meta reply kind')
        if ready and attachment:
            if message.role != 'agent' or content or not data:
                raise ValueError('Invalid Meta attachment reply')
            meta_outbound_media.stage(tenant_id, attachment, data)
            # Private storage can take time. Recheck ownership, binding and window after I/O.
            with Session() as db:
                current = db.get(Message, message_id)
                conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                    Conversation.tenant_id == tenant_id))
                tenant = db.get(Tenant, tenant_id)
                channel = db.scalar(select(Channel).where(Channel.id == conv.meta_channel_id,
                    Channel.tenant_id == tenant_id)) if conv else None
                ready = bool(current and conv and tenant and current.delivery_status == 'sending'
                             and eligible(current, conv, tenant, channel))
        # Detached scalar channel data; no database connection survives the HTTP call.
        if ready:
            attempted = True
            provider_id = (meta_outbound_media.send(channel, recipient, attachment, data) if attachment
                           else send(channel, recipient, content))
    except Exception:
        # Storage/provider details can contain credentials. Keep only the outcome below.
        pass
    handed_off = False
    with Session.begin() as db:
        current = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id).with_for_update())
        message = db.get(Message, message_id)
        if message is not None:
            message.channel_message_id = provider_id
            message.meta_acknowledged_at = now() if provider_id else None
            message.delivery_status = ('sent' if provider_id else
                ('unknown' if attempted or message.delivery_status == 'unknown' else 'failed'))
            if attachment:
                message.attachments = [{**attachment, 'status':
                    'ready' if attachment.get('status') == 'ready' else 'failed'}]
                message.upload_data = None
            if (not provider_id and message.role == 'assistant' and current
                    and current.status == 'bot' and not current.handoff):
                current.status, current.handoff, current.analytics_handoff = 'waiting', True, True
                handed_off = True
            message.error = None if provider_id else (
                'Meta delivery could not be confirmed; do not automatically retry'
                if message.delivery_status == 'unknown' else
                'Meta DM reply is no longer eligible or the messaging window expired')
    publish_sync(tenant_id, 'message.updated', {'conversation_id': conversation_id})
    if handed_off:
        publish_sync(tenant_id, 'conversation.updated', {'conversation_id': conversation_id})
    return True


def expire_stale() -> None:
    with Session.begin() as db:
        rows = db.execute(update(Message).where(
            Message.id.in_(select(Message.id).join(Conversation)
                           .where(Conversation.channel.in_(('instagram', 'messenger')))),
            Message.role.in_(('agent', 'assistant')), Message.delivery_status == 'sending',
            Message.dispatch_started_at < now() - timedelta(minutes=2),
        ).values(delivery_status='unknown', error='Meta delivery could not be confirmed', upload_data=None)
          .returning(Message.id, Message.conversation_id)).all()
        for message in db.scalars(select(Message).where(Message.id.in_([row.id for row in rows]))):
            message.attachments = [{**item, 'status': 'failed' if item['status'] == 'queued' else item['status']}
                                   for item in message.attachments]
        rows = [row.conversation_id for row in rows]
        changed = set(db.execute(update(Conversation).where(Conversation.id.in_(set(rows)),
            Conversation.status == 'bot', Conversation.handoff.is_(False))
            .values(status='waiting', handoff=True, analytics_handoff=True).returning(Conversation.id)).scalars())
        events = db.execute(select(Conversation.tenant_id, Conversation.id)
                            .where(Conversation.id.in_(set(rows)))).all() if rows else []
    for tenant_id, conversation_id in events:
        publish_sync(tenant_id, 'message.updated', {'conversation_id': conversation_id})
        if conversation_id in changed:
            publish_sync(tenant_id, 'conversation.updated', {'conversation_id': conversation_id})
