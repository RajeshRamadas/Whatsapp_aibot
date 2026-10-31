"""WhatsApp Cloud API adapter.

Inbound: Meta POSTs to /webhooks/whatsapp. We check the X-Hub-Signature-256
HMAC, record each message ID once (Meta retries deliveries), answer 200
straight away and reply from a background task.
"""
import hashlib
import hmac
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import rag
from .config import settings
from .db import Conversation, InboundEvent, Session, Tenant, uid

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
                if kind == 'text':
                    text = (msg.get('text') or {}).get('body') or ''
                elif kind == 'button':
                    text = (msg.get('button') or {}).get('text') or ''
                elif kind == 'interactive':
                    reply = (msg.get('interactive') or {})
                    reply = reply.get('button_reply') or reply.get('list_reply') or {}
                    text = reply.get('title') or ''
                else:
                    text = ''
                found.append({'phone_number_id': phone_number_id, 'from': msg['from'], 'id': msg['id'],
                              'type': kind, 'text': text.strip()})
    return found


def accept(message: dict) -> str | None:
    """Persist an inbound event once; return its durable ID only on first receipt."""
    with Session() as db:
        tenant_id = db.scalar(select(Tenant.id).where(Tenant.whatsapp_phone_number_id == message['phone_number_id']))
        if not tenant_id:
            log.warning('WhatsApp message for unknown phone_number_id %s', message['phone_number_id'])
            return None
        event = InboundEvent(id=uid(), channel_message_id=message['id'], tenant_id=tenant_id,
                             payload=message, status='queued', attempts=0)
        db.add(event)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        return event.id


def send_text(phone_number_id: str, to: str, body: str) -> None:
    response = httpx.post(
        f'https://graph.facebook.com/{settings.whatsapp_graph_version}/{phone_number_id}/messages',
        headers={'Authorization': f'Bearer {settings.whatsapp_access_token}'},
        json={'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': to,
              'type': 'text', 'text': {'preview_url': False, 'body': body[:MAX_TEXT]}},
        timeout=20)
    response.raise_for_status()


def conversation_for(tenant_id: str, wa_user: str) -> Conversation:
    query = select(Conversation).where(Conversation.tenant_id == tenant_id, Conversation.channel == 'whatsapp',
                                       Conversation.external_user == wa_user)
    with Session() as db:
        conv = db.scalar(query)
        if conv:
            return conv
        db.add(Conversation(tenant_id=tenant_id, channel='whatsapp', external_user=wa_user))
        try:
            db.commit()
        except IntegrityError:  # created concurrently by another message from the same user
            db.rollback()
        return db.scalar(query)


def handle(tenant_id: str, message: dict) -> None:
    """Answer a worker-owned inbound turn. Failures propagate to the retry policy."""
    phone_number_id, wa_user, text = message['phone_number_id'], message['from'], message['text']
    with Session() as db:
        tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        return
    conv = conversation_for(tenant_id, wa_user)
    if not text:
        placeholder = ('user', f"[{message['type']} message]")
        if conv.handoff:
            rag.save_exchange(conv, placeholder)
        else:
            send_text(phone_number_id, wa_user, UNSUPPORTED_REPLY)
            rag.save_exchange(conv, placeholder, ('assistant', UNSUPPORTED_REPLY))
        return
    if conv.handoff:
        rag.save_exchange(conv, ('user', text))
        return
    if text.lower() in settings.handoff_keywords:
        send_text(phone_number_id, wa_user, HANDOFF_REPLY)
        with Session.begin() as db:
            db.get(Conversation, conv.id).handoff = True
        rag.save_exchange(conv, ('user', text), ('assistant', HANDOFF_REPLY))
        return
    if not rag.chat_limiter.allow(f'wa:{tenant_id}:{wa_user}'):
        send_text(phone_number_id, wa_user, RATE_LIMIT_REPLY)
        return
    reply = rag.answer(tenant, conv.id, text).text or ERROR_REPLY
    send_text(phone_number_id, wa_user, reply)
    rag.save_exchange(conv, ('user', text), ('assistant', reply))
