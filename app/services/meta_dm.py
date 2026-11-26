"""Publish queued Instagram/Messenger text DMs to the tenant inbox."""
from sqlalchemy import or_, select

from ..db import Channel, Contact, Conversation, Message, MetaDmEvent, Session, now, uid
from .events import publish_sync


def process_one() -> bool:
    with Session.begin() as db:
        event = db.scalar(select(MetaDmEvent).where(MetaDmEvent.status == 'queued')
                          .order_by(MetaDmEvent.received_at, MetaDmEvent.id)
                          .with_for_update(skip_locked=True).limit(1))
        if event is None:
            return False
        tenant_id, channel, sender = event.tenant_id, event.channel, event.sender_id
        owner = db.scalar(select(Channel).where(Channel.id == event.channel_id, Channel.tenant_id == tenant_id,
                                                Channel.kind == channel, Channel.external_id == event.recipient_id))
        if owner is None:
            event.status = 'ignored'
            return True
        contact = db.scalar(select(Contact).where(Contact.tenant_id == tenant_id, Contact.channel == channel,
                                                  Contact.external_user == sender))
        if contact is None:
            contact = Contact(id=uid(), tenant_id=tenant_id, channel=channel, external_user=sender)
            db.add(contact)
            db.flush()
        elif db.scalar(select(Conversation.id).where(Conversation.contact_id == contact.id,
                Conversation.tenant_id == tenant_id,
                or_(Conversation.meta_channel_id.is_(None), Conversation.meta_channel_id != owner.id)).limit(1)):
            # Asset-scoped Meta IDs must not inherit another asset's private contact details.
            contact = None
        conv = db.scalar(select(Conversation).where(Conversation.tenant_id == tenant_id,
                                                    Conversation.channel == channel,
                                                    Conversation.meta_channel_id == owner.id,
                                                    Conversation.external_user == sender))
        if conv is None:
            conv = Conversation(id=uid(), tenant_id=tenant_id, channel=channel,
                                meta_channel_id=owner.id,
                                status='waiting', handoff=True, analytics_handoff=True,
                                external_user=sender, contact_id=contact.id if contact else None)
            db.add(conv)
            db.flush()
        if conv.contact_id is None and contact:
            conv.contact_id = contact.id
        # The event is locked; status update and message commit are atomic.
        db.add(Message(conversation_id=conv.id, role='user', direction='inbound',
                       channel_message_id=event.provider_message_id, content=event.text))
        if event.sent_at and (conv.last_inbound_at is None or event.sent_at > conv.last_inbound_at):
            conv.last_inbound_at = event.sent_at
        conv.last_message_at = now()
        event.status = 'processed'
        conversation_id = conv.id
    publish_sync(tenant_id, 'message.created', {'conversation_id': conversation_id})
    publish_sync(tenant_id, 'conversation.updated', {'conversation_id': conversation_id})
    return True
