"""Payment links and verified receipts: exactly one durable notice per milestone."""
from datetime import timedelta

from sqlalchemy import and_, or_, select

from .. import whatsapp
from ..db import Contact, Conversation, Message, Session, Template, Tenant, now
from ..models.payments import PaymentConnection as Connection
from ..models.payments import PaymentRequest as Request
from .appointments import row
from .channels import whatsapp_channel
from .contacts import marketing_eligible
from .customer_languages import normalize
from .events import publish_sync
from .payment_chat import amount, notice
from .payments import view
from .templates import render, send_payload, variable_count


def stage(db, value):
    if value.state not in ('created', 'paid') or value.cancel_requested and value.state != 'paid':
        return
    if not value.conversation_id or value.state == 'created' and not value.url:
        return
    kind = 'receipt' if value.state == 'paid' else 'link'
    if getattr(value, kind + '_message_id'):
        return
    conversation = row(db, Conversation, value.tenant_id, value.conversation_id)
    content = notice(db.get(Tenant, value.tenant_id), view(db, value))
    message = Message(conversation_id=conversation.id, role='agent' if conversation.channel == 'web' else 'assistant',
        direction='outbound', content=content, reply_key=f'payment:{value.id}:{kind}',
        delivery_status='sent' if conversation.channel == 'web' else 'queued')
    db.add(message)
    db.flush()
    setattr(value, kind + '_message_id', message.id)


def process_one():
    with Session.begin() as db:
        message = db.scalar(select(Message).join(Conversation).where(Message.reply_key.startswith('payment:'), or_(
            Message.delivery_status == 'queued', and_(Message.delivery_status == 'sending',
                Message.dispatch_started_at < now() - timedelta(minutes=2))))
            .order_by(Message.id).with_for_update(skip_locked=True, of=Message).limit(1))
        if not message:
            return False
        conversation = db.get(Conversation, message.conversation_id)
        tenant_id, conversation_id, message_id = conversation.tenant_id, conversation.id, message.id
        if message.delivery_status == 'sending':
            message.delivery_status, message.error = 'unknown', 'delivery_unknown'
            return True
        value = db.scalar(select(Request).where(Request.tenant_id == tenant_id,
            Request.conversation_id == conversation_id, or_(Request.link_message_id == message.id,
                                                            Request.receipt_message_id == message.id)))
        tenant = db.get(Tenant, tenant_id)
        channel = whatsapp_channel(db, tenant_id)
        phone = channel.external_id if channel else tenant.whatsapp_phone_number_id
        contact = db.scalar(select(Contact).where(Contact.id == conversation.contact_id,
            Contact.tenant_id == tenant_id)) if conversation.contact_id else None
        blocked = (not value or tenant.suspended or conversation.channel != 'whatsapp' or not phone
            or conversation.status == 'closed' or contact and contact.opt_out_at and (
                not contact.opt_in_at or contact.opt_out_at >= contact.opt_in_at))
        payload = None
        outside = not conversation.last_inbound_at or conversation.last_inbound_at < now() - timedelta(hours=24)
        if value and outside:
            connection = row(db, Connection, tenant_id, value.connection_id)
            template = db.scalar(select(Template).where(Template.id == connection.receipt_template_id,
                Template.channel_id == channel.id, Template.category == 'UTILITY', Template.status == 'APPROVED',
                Template.body.is_not(None))) if channel else None
            if (message.id != value.receipt_message_id or not contact or not marketing_eligible(contact)
                    or not template or normalize(template.language) != value.language):
                blocked = True
            else:
                fields = [value.description, amount(value.amount), value.payment_id or value.provider_id]
                try:
                    if variable_count(template.body) != 3:
                        raise ValueError()
                    message.content = render(template.body, fields)
                    payload = send_payload(template, fields)
                    message.kind, message.template_payload = 'template', payload
                except ValueError:
                    blocked = True
        if value and message.id == value.link_message_id and (
                value.cancel_requested or value.state != 'created' or value.expires_at <= now()):
            blocked = True
        if blocked:
            message.delivery_status, message.error = 'failed', 'channel_permission_required'
            return True
        message.delivery_status, message.dispatch_started_at = 'sending', now()
        content, recipient = message.content, conversation.external_user
    provider_id = None
    try:
        if payload:
            response = whatsapp.graph_message(phone, {'recipient_type': 'individual', 'to': recipient,
                'type': 'template', 'template': payload}, tenant_id)
            provider_id = (response.json().get('messages') or [{}])[0].get('id')
        else:
            provider_id = whatsapp.send_text(phone, recipient, content, tenant_id=tenant_id)
    except Exception:
        pass  # Uncertain sends are never repeated automatically.
    with Session.begin() as db:
        message = db.scalar(select(Message).join(Conversation).where(Message.id == message_id,
            Conversation.tenant_id == tenant_id,
            Message.conversation_id == conversation_id).with_for_update(of=Message))
        if message:
            message.delivery_status, message.channel_message_id = ('sent' if provider_id else 'unknown'), provider_id
            message.error = None if provider_id else 'delivery_unknown'
    publish_sync(tenant_id, 'message.created', {'conversation_id': conversation_id})
    return True
