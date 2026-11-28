"""Durable, opt-in Meta knowledge replies; all model work uses detached data."""
from datetime import timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.orm import aliased

from .. import rag
from ..config import settings
from ..db import AnswerReview, Channel, Conversation, Message, Session, Tenant, now, uid
from ..models.meta_bot import MetaBotTurn as Turn
from .bot_policy import within_hours
from .customer_languages import COPY, copy_for
from .events import publish_sync
from .usage import QUOTA_NOTICE, blocked_notice, reserve
from .whatsapp_copy import format_reply, handoff_notice


def wants_handoff(text):
    return text.strip().strip('*_').casefold() in settings.handoff_keywords


def window(at):
    return bool(at and now() - timedelta(hours=24) < at <= now())


def ready(tenant, channel, conv):
    return bool(tenant and not tenant.suspended and tenant.bot_live and channel and channel.meta_send_ready
        and channel.meta_bot_enabled and channel.tenant_id == tenant.id and conv and conv.tenant_id == tenant.id
        and conv.meta_channel_id == channel.id and conv.channel == channel.kind and window(conv.last_inbound_at))


def context(db, turn):
    tenant = db.get(Tenant, turn.tenant_id)
    channel = db.scalar(select(Channel).where(Channel.id == turn.channel_id, Channel.tenant_id == turn.tenant_id))
    conv = db.scalar(select(Conversation).where(Conversation.id == turn.conversation_id,
                                               Conversation.tenant_id == turn.tenant_id))
    return tenant, channel, conv


def stale(db, turn):
    return db.scalar(select(Message.id).where(Message.conversation_id == turn.conversation_id,
        Message.role == 'user', Message.id > turn.incoming_message_id).limit(1)) is not None


def cancel(db, conv):
    if conv.channel not in ('instagram', 'messenger'):
        return
    db.execute(update(Turn).where(Turn.tenant_id == conv.tenant_id, Turn.conversation_id == conv.id,
        Turn.status.in_(('queued', 'processing'))).values(status='cancelled', claim_token=None))
    db.execute(update(Message).where(Message.conversation_id == conv.id, Message.role == 'assistant',
        Message.delivery_status == 'queued').values(delivery_status='failed', error='Bot reply cancelled'))


def handoff(conv):
    conv.status, conv.handoff, conv.analytics_handoff = 'waiting', True, True


def notice(tenant, kind, question=None, reason=None):
    copy = copy_for(tenant, question)
    body = (copy['quota'] if blocked_notice(tenant.id) == QUOTA_NOTICE else copy['billing']) if reason == 'quota' else (
        handoff_notice(tenant, now(), copy))
    return body if len(body.encode('utf-8')) <= (1000 if kind == 'instagram' else 2000) else (
        COPY[copy['_language']]['handoff'])


def expire_stale():
    events = []
    with Session.begin() as db:
        rows = db.scalars(select(Turn).where(Turn.status == 'processing',
            Turn.claimed_at < now() - timedelta(minutes=10)).with_for_update(skip_locked=True).limit(100)).all()
        for row in rows:
            tenant, channel, _ = context(db, row)
            conv = db.scalar(select(Conversation).where(Conversation.id == row.conversation_id,
                Conversation.tenant_id == row.tenant_id).with_for_update(skip_locked=True))
            if not conv:
                continue
            row.status, row.claim_token, row.error = 'failed', None, 'interrupted'
            if conv.status == 'bot' and not conv.handoff:
                handoff(conv)
                if ready(tenant, channel, conv):
                    db.add(Message(conversation_id=conv.id, role='assistant', direction='outbound',
                        content=notice(tenant, conv.channel), delivery_status='queued',
                        reply_key='meta-notice:' + row.id))
                events.append((row.tenant_id, conv.id))
    for tenant_id, identity in events:
        publish_sync(tenant_id, 'conversation.updated', {'conversation_id': identity})
        publish_sync(tenant_id, 'message.created', {'conversation_id': identity})


def process_one():
    other = aliased(Turn)
    claimed = False
    with Session.begin() as db:
        turn = db.scalar(select(Turn).where(Turn.status == 'queued', ~exists(select(other.id).where(
            other.conversation_id == Turn.conversation_id, other.status.in_(('queued', 'processing')),
            other.incoming_message_id < Turn.incoming_message_id)))
            .order_by(Turn.incoming_message_id).with_for_update(skip_locked=True).limit(1))
        if not turn:
            return False
        tenant, channel, conv = context(db, turn)
        invalid = not ready(tenant, channel, conv)
        if invalid or conv.status != 'bot' or conv.handoff or stale(db, turn):
            turn.status = 'cancelled'
            if invalid and conv and conv.status == 'bot' and not conv.handoff:
                handoff(conv)
        else:
            incoming = db.scalar(select(Message).where(Message.id == turn.incoming_message_id,
                Message.conversation_id == conv.id, Message.role == 'user'))
            if incoming is None:
                turn.status = 'failed'
                handoff(conv)
            else:
                turn.status, turn.claimed_at, turn.claim_token = 'processing', now(), uid()
                identity, tenant_id, claim, question = turn.id, tenant.id, turn.claim_token, incoming.content
                first = not db.scalar(select(Message.id).where(Message.conversation_id == conv.id,
                    Message.role == 'assistant', Message.delivery_status.in_(('sent', 'delivered', 'read'))).limit(1))
                claimed = True
    if not claimed:
        if conv:
            publish_sync(turn.tenant_id, 'conversation.updated', {'conversation_id': conv.id})
        return True
    # No Session survives the quota check, embedding, model or grounding calls.
    copy, result, reason = copy_for(tenant, question), None, None
    try:
        if wants_handoff(question) or not within_hours(tenant):
            reason = 'handoff'
        elif not rag.chat_limiter.allow('meta:' + tenant_id + ':' + conv.id):
            reason = 'rate'
        elif not reserve(tenant_id, 'meta:' + identity):
            reason = 'quota'
        else:
            result = rag.answer(tenant, conv.id, question, knowledge_only=True,
                                history_before=turn.incoming_message_id, language=copy['_language'])
            if (result.uncertain or result.grounding_blocked or not result.grounded or not result.text.strip()
                    or result.appointment or result.transactional):
                reason = 'grounding'
    except Exception:
        reason = 'generation_failed'  # Never retain provider bodies, question text or credential values.
    sources = result.sources if result and not reason else []
    try:
        greeting = copy['greeting'].format(bot=tenant.bot_name, business=tenant.name) if first else ''
        body = format_reply(result.text, sources, copy, greeting, truncate=False) if result and not reason else ''
        if len(body.encode('utf-8')) > (1000 if conv.channel == 'instagram' else 2000):
            reason = 'too_long'
    except ValueError:
        reason = 'too_long'
    if reason:
        body, sources = notice(tenant, conv.channel, question, reason), []
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        db.scalar(select(Conversation).where(Conversation.id == turn.conversation_id,
            Conversation.tenant_id == tenant_id).with_for_update())
        current = db.scalar(select(Turn).where(Turn.id == identity, Turn.tenant_id == tenant_id).with_for_update())
        if not current or current.status != 'processing' or current.claim_token != claim:
            return True
        active, channel, conv = context(db, current)
        if conv:
            db.refresh(conv, with_for_update=True)
        invalid = not ready(active, channel, conv)
        queued = not (invalid or conv.status != 'bot' or conv.handoff or stale(db, current))
        if not queued:
            current.status, current.claim_token = 'cancelled', None
            if invalid and conv and conv.status == 'bot' and not conv.handoff:
                handoff(conv)
        else:
            if not within_hours(active) and not reason:
                reason, sources, body = 'handoff', [], notice(active, conv.channel, question)
            if reason:
                handoff(conv)
            prefix = 'meta-notice:' if reason else 'meta-bot:'
            message = Message(conversation_id=conv.id, role='assistant', direction='outbound', kind='text',
                content=body, sources=sources, delivery_status='queued', reply_key=prefix + current.id)
            db.add(message)
            if result:
                db.flush()
                db.add(AnswerReview(tenant_id=tenant_id, message_id=message.id,
                    question=rag.http_action_chat.redact_command(question), unanswered=bool(reason or not sources)))
            conv.last_message_at = now()
            current.status, current.claim_token, current.error = 'complete', None, reason
    if queued:
        publish_sync(tenant_id, 'message.created', {'conversation_id': conv.id})
    publish_sync(tenant_id, 'conversation.updated', {'conversation_id': conv.id})
    return True
