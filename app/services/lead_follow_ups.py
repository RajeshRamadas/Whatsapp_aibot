"""Find quiet leads and claim a single, consented WhatsApp template send."""
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select, update

from .. import whatsapp
from ..config import settings
from ..db import Channel, Contact, Conversation, Lead, Message, Session, Tenant, now
from ..models.lead_follow_ups import FOLLOW_UP_REPLY_KEY
from ..models.lead_follow_ups import LeadFollowUpRule as Rule
from . import campaign_delivery
from .campaigns import CampaignError
from .channels import whatsapp_channel
from .contacts import eligible
from .events import publish_sync
from .templates import render, send_payload, template_for_send

CATEGORIES = ('MARKETING', 'UTILITY')
OPEN_LEADS = ('new', 'contacted', 'qualified')


@dataclass(frozen=True)
class Delivery:
    tenant_id: str
    conversation_id: str
    message_id: int
    phone_id: str
    recipient: str
    template: dict


def snapshot(template, channel):
    return {'channel_id': channel.id, 'phone_id': channel.external_id, 'waba_id': channel.waba_id,
            'name': template.name, 'language': template.language, 'body': template.body, 'category': template.category}


def configured_template(db, rule, channel):
    if not channel or not channel.waba_id or rule.template_id is None:
        return None
    template = template_for_send(db, channel.id, rule.template_id)
    if template and snapshot(template, channel) == rule.snapshot:
        return template
    return None


def candidates(point):
    attempted = select(Message.id).where(Message.conversation_id == Conversation.id,
                                         Message.reply_key == FOLLOW_UP_REPLY_KEY).exists()
    return select(Lead, Conversation, Contact).join(Conversation, (
        (Conversation.id == Lead.conversation_id) & (Conversation.tenant_id == Lead.tenant_id)
    )).join(Contact, (Contact.id == Conversation.contact_id) & (Contact.tenant_id == Lead.tenant_id)).where(
        Lead.tenant_id == Rule.tenant_id, Lead.status.in_(OPEN_LEADS), Conversation.channel == 'whatsapp',
        Conversation.status != 'closed', Conversation.external_user == Contact.external_user, eligible(),
        Conversation.last_inbound_at <= Lead.created_at, ~attempted,
        func.greatest(Lead.created_at, Rule.configured_at) + func.make_interval(0, 0, 0, 0, Rule.delay_hours) <= point,
    )


def pending():
    if not settings.whatsapp_enabled:
        return []
    point = now()
    with Session.begin() as db:
        db.execute(update(Message).where(Message.reply_key == FOLLOW_UP_REPLY_KEY, Message.delivery_status == 'sending',
            Message.dispatch_started_at < point - timedelta(minutes=2))
            .values(delivery_status='unknown', error='delivery_unknown'))
        due = candidates(point).with_only_columns(Lead.conversation_id).correlate(Rule).exists()
        return db.execute(select(Rule.tenant_id, Rule.next_check_at).join(Tenant, Tenant.id == Rule.tenant_id).where(
            Rule.enabled.is_(True), Rule.template_id.is_not(None), Rule.next_check_at <= point,
            Tenant.suspended.is_(False), due,
        ).order_by(Rule.next_check_at, Rule.tenant_id).limit(100)).all()


def reserve_check(tenant_id):
    with Session.begin() as db:
        rule = db.get(Rule, tenant_id, with_for_update=True)
        point = now()
        tenant = db.get(Tenant, tenant_id)
        if not rule or not rule.enabled or rule.next_check_at > point or not tenant or tenant.suspended:
            return None
        rule.next_check_at = point + timedelta(minutes=1)
        channel = whatsapp_channel(db, tenant_id)
        if not configured_template(db, rule, channel):
            return None
        return channel.id, rule.next_check_at


def claim(tenant_id: str, channel_id: str, reserved_until: datetime) -> Delivery | None:
    with Session.begin() as db:
        rule = db.get(Rule, tenant_id, with_for_update=True)
        tenant = db.get(Tenant, tenant_id)
        if not rule or not rule.enabled or rule.next_check_at != reserved_until or not tenant or tenant.suspended:
            return None
        channel = db.scalar(select(Channel).where(Channel.id == channel_id, Channel.tenant_id == tenant_id,
            Channel.kind == 'whatsapp').with_for_update())
        template = configured_template(db, rule, channel)
        if (not template or channel.campaign_quality != 'GREEN' or not channel.campaign_portfolio
                or channel.campaign_limit < 1):
            return None
        point = now()
        result = db.execute(candidates(point).where(Rule.tenant_id == tenant_id)
            .order_by(Lead.created_at, Lead.conversation_id).limit(1)
            .with_for_update(of=(Lead, Conversation, Contact), skip_locked=True)).first()
        if not result:
            return None
        _, conversation, contact = result
        if not campaign_delivery.capacity_available(db, channel, contact.external_user):
            return None
        if not campaign_delivery.send_rate_available(channel.external_id):
            return None
        message = Message(conversation_id=conversation.id, role='assistant', direction='outbound', kind='template',
            content=render(template.body, rule.values), reply_key=FOLLOW_UP_REPLY_KEY,
            template_payload={'portfolio_id': channel.campaign_portfolio, 'phone_id': channel.external_id},
            delivery_status='sending', dispatch_started_at=point)
        db.add(message)
        db.flush()
        conversation.last_message_at = point
        rule.next_check_at = point
        return Delivery(tenant_id, conversation.id, message.id, channel.external_id, contact.external_user,
                        send_payload(template, rule.values))


def deliver(tenant_id):
    if not settings.whatsapp_enabled or not (reserved := reserve_check(tenant_id)):
        return
    channel_id, reserved_until = reserved
    try:
        available = campaign_delivery.refresh(channel_id, tenant_id)
    except CampaignError:
        return  # The channel was unlinked before the quality check.
    if not available:
        return
    delivery = claim(tenant_id, channel_id, reserved_until)
    if delivery is None:
        return
    # The durable claim is committed before HTTP. Ambiguous outcomes consume the claim.
    provider_id = None
    state, error = 'unknown', 'delivery_unknown'
    quality_warning = False
    try:
        response = whatsapp.graph_message(delivery.phone_id, {'recipient_type': 'individual', 'to': delivery.recipient,
            'type': 'template', 'template': delivery.template,
            'biz_opaque_callback_data': f'{FOLLOW_UP_REPLY_KEY}:{delivery.message_id}'}, tenant_id)
        candidate = response.json()['messages'][0]['id']
        if isinstance(candidate, str) and 1 <= len(candidate) <= 128:
            provider_id, state, error = candidate, 'sent', None
    except httpx.HTTPStatusError as exc:
        if 400 <= exc.response.status_code < 500:
            state, error = 'failed', 'provider_rejected'
        quality_warning = campaign_delivery.provider_error_code(exc.response) == 131048
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, RuntimeError):
        pass
    with Session.begin() as db:
        message = db.scalar(select(Message).join(Conversation, Conversation.id == Message.conversation_id).where(
            Message.id == delivery.message_id, Conversation.tenant_id == tenant_id).with_for_update(of=Message))
        if message and message.delivery_status in ('sending', 'unknown') and message.channel_message_id is None:
            message.channel_message_id, message.delivery_status, message.error = provider_id, state, error
    if quality_warning:
        try:
            campaign_delivery.quality(channel_id, tenant_id, 'RED')
        except CampaignError:
            pass  # An unlinked channel can no longer send.
    publish_sync(tenant_id, 'message.created', {'conversation_id': delivery.conversation_id})
    publish_sync(tenant_id, 'conversation.updated', {'conversation_id': delivery.conversation_id})
