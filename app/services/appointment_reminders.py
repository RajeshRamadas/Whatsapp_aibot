"""Durable, consent-aware appointment reminders. Never retry uncertain WhatsApp sends."""
from datetime import timedelta

from sqlalchemy import and_, or_, select

from .. import whatsapp
from ..db import Contact, Conversation, Message, Session, Template, Tenant, now
from ..models.appointments import Booking, Service, Slot
from .appointment_chat import local_time, notice
from .appointments import booking_view, row
from .channels import whatsapp_channel
from .contacts import marketing_eligible
from .customer_languages import normalize
from .events import publish_sync
from .templates import render, send_payload


def process_one():
    with Session.begin() as db:
        booking = db.scalar(select(Booking).where(Booking.state == 'confirmed', Booking.reminder_at <= now(), or_(
            Booking.reminder_state == 'pending', and_(Booking.reminder_state == 'sending',
                Booking.claimed_at < now() - timedelta(minutes=2)),
        )).order_by(Booking.reminder_at, Booking.id).limit(1).with_for_update(skip_locked=True))
        if booking is None:
            return False
        tenant_id, identity = booking.tenant_id, booking.id
        tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id))
        conversation = row(db, Conversation, tenant_id, booking.conversation_id)
        slot = row(db, Slot, tenant_id, booking.slot_id)
        if booking.reminder_state == 'sending':
            booking.reminder_state, booking.reminder_error = 'unknown', 'delivery_unknown'
            if booking.reminder_message_id:
                message = db.scalar(select(Message).where(Message.id == booking.reminder_message_id,
                                                           Message.conversation_id == conversation.id))
                if message:
                    message.delivery_status = 'unknown'
            return True
        if not booking.reminder_opt_in or slot.starts_at <= now():
            booking.reminder_state = 'skipped'
            return True
        contact = db.scalar(select(Contact).where(Contact.id == conversation.contact_id,
            Contact.tenant_id == tenant_id)) if conversation.contact_id else None
        opted_out = contact and contact.opt_out_at and (
            not contact.opt_in_at or contact.opt_out_at >= contact.opt_in_at)
        blocked = ('workspace_suspended' if tenant.suspended else 'conversation_closed'
                   if conversation.status == 'closed' else 'opted_out' if opted_out else None)
        template_payload = None
        value = booking_view(db, booking)
        content = notice(tenant, value, 'reminder')
        channel = whatsapp_channel(db, tenant_id)
        phone = channel.external_id if channel else tenant.whatsapp_phone_number_id
        recipient = conversation.external_user
        if conversation.channel == 'whatsapp':
            if not phone or recipient != booking.customer_phone.lstrip('+'):
                blocked = 'channel_unavailable'
            if not conversation.last_inbound_at or conversation.last_inbound_at <= now() - timedelta(hours=24):
                service = row(db, Service, tenant_id, slot.service_id)
                template = db.scalar(select(Template).where(Template.id == service.reminder_template_id,
                    Template.channel_id == channel.id, Template.category == 'UTILITY',
                    Template.status == 'APPROVED', Template.body.is_not(None))) if channel else None
                if not contact or not marketing_eligible(contact):
                    blocked = blocked or 'template_opt_in_required'
                elif not template:
                    blocked = blocked or 'approved_template_required'
                elif normalize(template.language) != booking.language:
                    blocked = blocked or 'template_language_mismatch'
                else:
                    # Reuse the exact local time from the booking, never model-produced values.
                    values = [value['service'], value['staff'], local_time(tenant, slot.starts_at)]
                    try:
                        content = render(template.body, values)
                        template_payload = send_payload(template, values)
                    except ValueError:
                        blocked = blocked or 'template_changed'
        elif conversation.channel != 'web':
            blocked = 'channel_unavailable'
        if blocked:
            booking.reminder_state, booking.reminder_error = 'blocked', blocked
            return True
        message = Message(conversation_id=conversation.id,
            role='agent' if conversation.channel == 'web' else 'assistant',
            direction='outbound', kind='template' if template_payload else 'text', content=content,
            reply_key='appointment:' + identity, delivery_status='sent' if conversation.channel == 'web' else 'sending',
            dispatch_started_at=now())
        db.add(message)
        db.flush()
        booking.reminder_message_id, booking.claimed_at = message.id, now()
        booking.reminder_state = 'sent' if conversation.channel == 'web' else 'sending'
        message_id, conversation_id, channel_kind = message.id, conversation.id, conversation.channel
    # The claim and customer message are committed before any provider call.
    if channel_kind == 'whatsapp':
        provider_id = None
        try:
            if template_payload:
                response = whatsapp.graph_message(phone, {'recipient_type': 'individual', 'to': recipient,
                    'type': 'template', 'template': template_payload}, tenant_id)
                provider_id = (response.json().get('messages') or [{}])[0].get('id')
            else:
                provider_id = whatsapp.send_text(phone, recipient, content, tenant_id=tenant_id)
        except Exception:
            pass  # Delivery may have succeeded. Surface uncertainty; do not automatically resend.
        with Session.begin() as db:
            booking = db.scalar(select(Booking).where(Booking.id == identity, Booking.tenant_id == tenant_id)
                                .with_for_update())
            message = db.scalar(select(Message).where(Message.id == message_id,
                                                       Message.conversation_id == conversation_id))
            state = 'sent' if provider_id else 'unknown'
            if message:
                message.delivery_status, message.channel_message_id = state, provider_id
            if booking and booking.reminder_state == 'sending':
                booking.reminder_state = state
                booking.reminder_error = None if provider_id else 'delivery_unknown'
    publish_sync(tenant_id, 'message.created', {'conversation_id': conversation_id})
    publish_sync(tenant_id, 'appointments.updated', {})
    return True
