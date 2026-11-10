"""Transactional completion notices, with durable and uncertainty-safe WhatsApp dispatch."""
from datetime import timedelta

from sqlalchemy import and_, or_, select

from .. import whatsapp
from ..db import Contact, Conversation, Message, Session, Tenant, now
from .appointment_chat import notice
from .appointments import booking_view, row
from .channels import whatsapp_channel
from .events import publish_sync


def stage(db, booking):
    conversation = row(db, Conversation, booking.tenant_id, booking.conversation_id)
    key = f'calendar:{booking.id}:{booking.calendar_version}'
    if db.scalar(select(Message.id).where(Message.conversation_id == conversation.id, Message.reply_key == key)):
        return
    value = booking_view(db, booking)
    content = notice(db.get(Tenant, booking.tenant_id), value, 'confirmed' if booking.state == 'confirmed' else 'error')
    db.add(Message(conversation_id=conversation.id, role='agent' if conversation.channel == 'web' else 'assistant',
        direction='outbound', content=content, reply_key=key,
        delivery_status='sent' if conversation.channel == 'web' else 'queued'))


def process_one():
    with Session.begin() as db:
        message = db.scalar(select(Message).join(Conversation).where(Message.reply_key.startswith('calendar:'), or_(
            Message.delivery_status == 'queued', and_(Message.delivery_status == 'sending',
            Message.dispatch_started_at < now() - timedelta(minutes=2))))
            .order_by(Message.id).with_for_update(skip_locked=True).limit(1))
        if not message:
            return False
        conversation = db.get(Conversation, message.conversation_id)
        tenant_id, conversation_id, message_id = conversation.tenant_id, conversation.id, message.id
        if message.delivery_status == 'sending':
            message.delivery_status, message.error = 'unknown', 'delivery_unknown'
            return True
        tenant = db.get(Tenant, tenant_id)
        channel = whatsapp_channel(db, tenant_id)
        phone = channel.external_id if channel else tenant.whatsapp_phone_number_id
        contact = db.scalar(select(Contact).where(Contact.id == conversation.contact_id,
                                                  Contact.tenant_id == tenant_id)) if conversation.contact_id else None
        if (tenant.suspended or conversation.channel != 'whatsapp' or conversation.status == 'closed' or not phone
                or not conversation.last_inbound_at or conversation.last_inbound_at < now() - timedelta(hours=24)
                or contact and contact.opt_out_at and (
                    not contact.opt_in_at or contact.opt_out_at >= contact.opt_in_at)):
            message.delivery_status, message.error = 'failed', 'channel_permission_required'
            return True
        message.delivery_status, message.dispatch_started_at = 'sending', now()
        content, recipient = message.content, conversation.external_user
    provider_id = None
    try:
        provider_id = whatsapp.send_text(phone, recipient, content, tenant_id=tenant_id)
    except Exception:
        pass
    with Session.begin() as db:
        message = db.scalar(select(Message).join(Conversation).where(Message.id == message_id,
            Conversation.tenant_id == tenant_id, Message.conversation_id == conversation_id).with_for_update())
        if message:
            message.delivery_status = 'sent' if provider_id else 'unknown'
            message.channel_message_id = provider_id
            message.error = None if provider_id else 'delivery_unknown'
    publish_sync(tenant_id, 'message.created', {'conversation_id': conversation_id})
    return True
