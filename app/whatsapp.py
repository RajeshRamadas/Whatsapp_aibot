"""WhatsApp Cloud API adapter.

Inbound: Meta POSTs to /webhooks/whatsapp. We check the X-Hub-Signature-256
HMAC, record each message ID once (Meta retries deliveries), answer 200
straight away and reply from a background task.
"""
import hashlib
import hmac
import logging
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import rag
from .config import settings
from .db import Contact, Conversation, InboundEvent, Message, Session, Tenant, now, uid
from .services.bot_policy import within_hours
from .services.channels import access_token, find_whatsapp, tenant_for_phone
from .services.events import publish_sync
from .services.usage import QUOTA_NOTICE, blocked_notice, reserve
from .services.whatsapp_copy import copy_for, format_reply, handoff_notice, interactive_payload

log = logging.getLogger(__name__)

MAX_TEXT = 4096  # WhatsApp text message limit
HANDOFF_REPLY = 'Thanks, a member of our team will reply here shortly.'
UNSUPPORTED_REPLY = 'Sorry, I can only read text messages for now.'
ERROR_REPLY = 'Sorry, something went wrong. Please try again in a moment.'
RATE_LIMIT_REPLY = "You're sending messages too quickly. Please wait a minute and try again."


def valid_signature(body: bytes, header: str | None) -> bool:
    if not header or not header.startswith('sha256='):
        return False
    expected = hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix('sha256='))


def parse_messages(payload: dict) -> list[dict]:
    """Flatten a webhook payload into {phone_number_id, from, id, type, text} dicts. Status updates are skipped."""
    found = []
    for entry in payload.get('entry') or []:
        for change in entry.get('changes') or []:
            value = change.get('value') or {}
            phone_number_id = (value.get('metadata') or {}).get('phone_number_id')
            for msg in value.get('messages') or []:
                if not (phone_number_id and msg.get('id') and msg.get('from')):
                    continue
                kind = msg.get('type')
                choice = None
                if kind == 'text':
                    text = (msg.get('text') or {}).get('body') or ''
                elif kind == 'button':
                    text = (msg.get('button') or {}).get('text') or ''
                elif kind == 'interactive':
                    reply = (msg.get('interactive') or {})
                    reply = reply.get('button_reply') or reply.get('list_reply') or {}
                    text = reply.get('title') or ''
                    choice = reply.get('id')
                else:
                    text = ''
                found.append({'phone_number_id': phone_number_id, 'from': msg['from'], 'id': msg['id'],
                              'type': kind, 'text': text.strip(), 'choice': choice})
    return found


def accept(message: dict) -> str | None:
    """Persist an inbound event once; return its durable ID only on first receipt."""
    with Session() as db:
        channel = find_whatsapp(db, message['phone_number_id'])
        tenant_id = tenant_for_phone(db, message['phone_number_id'], channel)
        if not tenant_id:
            log.warning('WhatsApp message for unknown phone_number_id %s', message['phone_number_id'])
            return None
        received_at = now()
        if channel is not None:
            channel.last_webhook_at = received_at
        conv = conversation_for(tenant_id, message['from'])
        current = db.get(Conversation, conv.id, with_for_update=True)
        if current.last_inbound_at is None or current.last_inbound_at < received_at:
            current.last_inbound_at = received_at
        event = InboundEvent(received_at=received_at, id=uid(), channel_message_id=message['id'], tenant_id=tenant_id,
                             payload=message, status='queued', attempts=0)
        db.add(event)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(InboundEvent).where(
                InboundEvent.channel_message_id == message['id'],
                InboundEvent.tenant_id == tenant_id))
            return existing.id if existing and existing.status == 'queued' else None
        return event.id


def graph_message(phone_number_id, payload, tenant_id, timeout=20):
    response = httpx.post(
        f'https://graph.facebook.com/{settings.whatsapp_graph_version}/{phone_number_id}/messages',
        headers={'Authorization': f'Bearer {access_token(phone_number_id, tenant_id)}'},
        json={'messaging_product': 'whatsapp', **payload},
        timeout=timeout)
    response.raise_for_status()
    return response


def send_text(phone_number_id: str, to: str, body: str, *, tenant_id: str | None = None,
              interactive: dict | None = None) -> str | None:
    payload = {'recipient_type': 'individual', 'to': to}
    if interactive is not None:
        payload.update(type='interactive', interactive=interactive)
    else:
        payload.update(type='text', text={'preview_url': False, 'body': body[:MAX_TEXT]})
    messages = graph_message(phone_number_id, payload, tenant_id).json().get('messages') or []
    return messages[0].get('id') if messages else None


def mark_typing(phone_id, message_id, tenant_id):
    if not message_id:
        return
    try:
        graph_message(phone_id, {'status': 'read', 'message_id': message_id,
                                 'typing_indicator': {'type': 'text'}}, tenant_id, timeout=2)
    except (httpx.HTTPError, ValueError, RuntimeError):
        log.debug('WhatsApp typing indicator unavailable')


def conversation_for(tenant_id: str, wa_user: str) -> Conversation:
    """Resolve or create a tenant-scoped contact and its WhatsApp conversation."""
    query = select(Conversation).where(Conversation.tenant_id == tenant_id, Conversation.channel == 'whatsapp',
                                       Conversation.external_user == wa_user)
    contact_query = select(Contact).where(Contact.tenant_id == tenant_id, Contact.channel == 'whatsapp',
                                          Contact.external_user == wa_user)
    with Session.begin() as db:
        contact = db.scalar(contact_query)
        if contact is None:
            contact = Contact(tenant_id=tenant_id, channel='whatsapp', external_user=wa_user)
            try:
                with db.begin_nested():
                    db.add(contact)
                    db.flush()
            except IntegrityError:
                contact = None
            if contact is None:
                contact = db.scalar(contact_query)
        conv = db.scalar(query)
        if conv is None:
            conv = Conversation(tenant_id=tenant_id, channel='whatsapp',
                                external_user=wa_user, contact_id=contact.id, status='bot')
            try:
                with db.begin_nested():
                    db.add(conv)
                    db.flush()
            except IntegrityError:
                conv = None
            if conv is None:
                conv = db.scalar(query)
        if conv.contact_id is None:
            conv.contact_id = contact.id
        return conv


def handle(tenant_id: str, message: dict) -> None:
    """Answer a worker-owned inbound turn. Failures propagate to the retry policy."""
    phone_id, wa_user, text = message['phone_number_id'], message['from'], message['text']
    with Session() as db:
        tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.suspended:
        return
    conv = conversation_for(tenant_id, wa_user)
    copy = copy_for(tenant)
    incoming = ('user', text or f"[{message['type']} message]")
    parts = message.get('parts', [message])
    keywords = {'stop', 'बंद', *tenant.whatsapp_opt_out_keywords}
    consent = next((value for part in reversed(parts)
                    if (value := part.get('text', '').strip().casefold()) in keywords | {'start'}), None)
    choices = {part.get('choice') for part in parts}
    wants_handoff = 'handoff' in choices or any(
        part.get('text', '').strip().lower() in settings.handoff_keywords for part in parts)
    timestamp = now()

    def reply(body, options=None, sources=None, lead_fields=None):
        interactive = interactive_payload(body, options, copy)
        message_id = send_text(phone_id, wa_user, body, tenant_id=tenant_id, interactive=interactive)
        rag.save_exchange(conv, incoming, ('assistant', body), channel_message_id=message_id,
                          sources=sources, lead_fields=lead_fields)

    if consent is not None:
        with Session.begin() as db:
            contact = db.get(Contact, conv.contact_id, with_for_update=True)
            if consent == 'start':
                contact.opt_in_at, contact.opt_out_at = timestamp, None
            else:
                contact.opt_out_at = timestamp
        reply(copy['start' if consent == 'start' else 'stop'])
        return

    if conv.handoff or conv.status in ('waiting', 'agent', 'closed'):
        if (tenant.bot_live and conv.status == 'waiting' and conv.last_message_at
                and timestamp - conv.last_message_at >= timedelta(hours=1)):
            reply(handoff_notice(tenant, timestamp))
        else:
            rag.save_exchange(conv, incoming)
        return

    open_now = within_hours(tenant, timestamp)
    if wants_handoff or not tenant.bot_live or not open_now:
        with Session() as db:
            active = db.get(Conversation, conv.id)
            should_notify = active.status == 'bot' and not active.handoff
        if should_notify and (wants_handoff or tenant.bot_live):
            reply(handoff_notice(tenant, timestamp))
        else:
            rag.save_exchange(conv, incoming)
        with Session.begin() as db:
            active = db.get(Conversation, conv.id, with_for_update=True)
            if active.status == 'bot' and not active.handoff:
                active.handoff = True
                active.status = 'waiting'
        publish_sync(tenant_id, 'conversation.updated', {'conversation_id': conv.id})
        return
    if 'handoff-decline' in choices:
        reply(copy['declined'])
        return
    if not text:
        reply(copy['unsupported'])
        return
    if not rag.chat_limiter.allow(f'wa:{tenant_id}:{wa_user}'):
        send_text(phone_id, wa_user, copy['rate'], tenant_id=tenant_id)
        return

    if not reserve(tenant_id, 'whatsapp:' + str(message.get('id') or uid())[:120]):
        notice = blocked_notice(tenant_id)
        if notice == QUOTA_NOTICE and copy['from'] != 'From: ':
            notice = 'स्वचालित सहायक की मासिक सीमा पूरी हो गई है। हमारी टीम आपकी मदद कर सकती है।'
        message_id = send_text(phone_id, wa_user, notice, tenant_id=tenant_id)
        rag.save_exchange(conv, incoming, ('assistant', notice), channel_message_id=message_id, waiting=True)
        return

    mark_typing(phone_id, parts[-1].get('id'), tenant_id)
    answer = rag.answer(tenant, conv.id, text, whatsapp=True)
    with Session() as db:
        current = db.get(Conversation, conv.id)
        can_send = (current is not None and not current.handoff and current.status == 'bot'
                    and not db.get(Tenant, tenant_id).suspended)
        has_history = db.scalar(select(Message.id).where(
            Message.conversation_id == conv.id, Message.role == 'user').limit(1)) is not None
    if not can_send:
        rag.save_exchange(conv, incoming)
        return
    greeting = '' if has_history else copy['greeting'].format(bot=tenant.bot_name, business=tenant.name)
    sources = answer.sources if answer.text and not answer.uncertain else []
    if answer.uncertain:
        options = [('handoff', copy['yes']), ('handoff-decline', copy['no'])]
        body = copy['unknown']
    else:
        options = [(f'option-{index}', title) for index, title in enumerate(answer.choices or [])]
        if len(options) < 3:
            options.append(('handoff', copy['team']))
        body = answer.text or copy['error']
    reply(format_reply(body, sources, copy, greeting), options, sources, answer.lead_fields)


DELIVERY_RANK = {'sent': 1, 'delivered': 2, 'read': 3}


def parse_statuses(payload: dict) -> list[dict]:
    """Extract delivery receipts with their sending phone-number identity."""
    found = []
    for entry in payload.get('entry') or []:
        for change in entry.get('changes') or []:
            value = change.get('value') or {}
            phone_id = (value.get('metadata') or {}).get('phone_number_id')
            for status in value.get('statuses') or []:
                if phone_id and status.get('id') and status.get('status') in (*DELIVERY_RANK, 'failed'):
                    found.append({'phone_number_id': phone_id, **status})
    return found


def record_status(status: dict) -> bool:
    """Apply a tenant-verified receipt without regressing a known delivery state."""
    with Session.begin() as db:
        channel = find_whatsapp(db, status['phone_number_id'])
        tenant_id = tenant_for_phone(db, status['phone_number_id'], channel)
        if channel is not None:
            channel.last_webhook_at = now()
        if not tenant_id:
            return False
        row = db.scalar(select(Message).join(Conversation, Message.conversation_id == Conversation.id)
                        .where(Conversation.tenant_id == tenant_id,
                               Conversation.channel == 'whatsapp',
                               Message.direction == 'outbound',
                               Message.channel_message_id == status['id'])
                        .with_for_update())
        if row is None:
            return False
        incoming = status['status']
        previous = row.delivery_status
        if previous == incoming:
            return True
        if previous == 'failed' or (incoming == 'failed' and previous in ('delivered', 'read')):
            return True
        if incoming != 'failed' and DELIVERY_RANK.get(incoming, 0) <= DELIVERY_RANK.get(previous, 0):
            return True
        row.delivery_status = incoming
        if incoming == 'failed':
            errors = status.get('errors') or []
            first = errors[0] if errors and isinstance(errors[0], dict) else {}
            code = str(first.get('code', 'unknown'))[:32]
            title = str(first.get('title', 'Delivery failed'))[:400]
            row.error = f'{code}: {title}'
        else:
            row.error = None
        return True
