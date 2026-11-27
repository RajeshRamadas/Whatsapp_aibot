"""Authenticated receipt metadata; no provider calls or inbound-window changes."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ..db import Channel, Conversation, Message, Session, now, uid
from ..models.meta_receipts import MetaReceipt
from .events import publish_sync

RANK = {'sent': 1, 'delivered': 2, 'read': 3}


def stamp(value):
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= int(now().timestamp() * 1000):
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    return None


def extract(payload):
    obj = payload.get('object')
    kind = {'page': 'messenger', 'instagram': 'instagram'}.get(obj) if isinstance(obj, str) else None
    entries = payload.get('entry')
    if not kind or not isinstance(entries, list):
        return
    for entry in entries:
        events = entry.get('messaging') if isinstance(entry, dict) else None
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            sender, recipient = event.get('sender'), event.get('recipient')
            if not isinstance(sender, dict) or not isinstance(recipient, dict):
                continue
            sender, recipient = sender.get('id'), recipient.get('id')
            if not all(isinstance(value, str) and 1 <= len(value) <= 64 for value in (sender, recipient)):
                continue
            for key, status in [('delivery', 'delivered'), ('read', 'read')]:
                body = event.get(key)
                if not isinstance(body, dict) or (kind == 'instagram' and key != 'read'):
                    continue
                mids = [body['mid']] if kind == 'instagram' and 'mid' in body else body.get('mids', [])
                if (not isinstance(mids, list) or len(mids) > 100 or any(
                        not isinstance(mid, str) or not 1 <= len(mid) <= 255 for mid in mids)):
                    continue
                mids = [] if kind == 'messenger' and key == 'read' else sorted(set(mids))
                watermark = stamp(body.get('watermark')) if kind == 'messenger' else None
                if not mids and (kind == 'instagram' or watermark is None):
                    continue
                # Messenger reads are watermarks; Instagram seen events identify a MID.
                if kind == 'messenger' and key == 'read' and watermark is None:
                    continue
                yield kind, sender, recipient, status, mids, watermark


def store(payload):
    accepted = 0
    for kind, sender, recipient, status, mids, watermark in extract(payload):
        digest = hashlib.sha256(json.dumps([sender, status, mids, watermark.isoformat() if watermark else None],
                                          separators=(',', ':')).encode()).hexdigest()
        with Session.begin() as db:
            # Signed asset identity is the sole pre-tenant routing lookup.
            channel = db.scalar(select(Channel).where(Channel.kind == kind, Channel.external_id == recipient))
            if not channel:
                continue
            result = db.execute(insert(MetaReceipt).values(id=uid(), tenant_id=channel.tenant_id,
                channel_id=channel.id, sender_id=sender, kind=status, mids=mids, watermark=watermark,
                dedupe_key=digest, status='queued', received_at=now(), next_attempt_at=now())
                .on_conflict_do_nothing(constraint='uq_meta_receipt').returning(MetaReceipt.id))
            accepted += int(result.scalar_one_or_none() is not None)
    return accepted


def cleanup():
    with Session.begin() as db:
        db.execute(delete(MetaReceipt).where(MetaReceipt.received_at < now() - timedelta(days=7)))


def process_one():
    changed, tenant_id, identity = False, None, None
    with Session.begin() as db:
        receipt = db.scalar(select(MetaReceipt).where(MetaReceipt.status == 'queued',
            MetaReceipt.next_attempt_at <= now()).order_by(MetaReceipt.received_at, MetaReceipt.id)
            .with_for_update(skip_locked=True).limit(1))
        if receipt is None:
            return False
        tenant_id = receipt.tenant_id
        channel = db.scalar(select(Channel).where(Channel.id == receipt.channel_id, Channel.tenant_id == tenant_id))
        conv = db.scalar(select(Conversation).where(Conversation.tenant_id == tenant_id,
            Conversation.meta_channel_id == receipt.channel_id, Conversation.external_user == receipt.sender_id,
            Conversation.channel == channel.kind).with_for_update()) if channel else None
        if not conv:
            pending = True
        else:
            identity = conv.id
            if receipt.kind == 'read' and receipt.watermark and (
                    conv.meta_read_watermark is None or receipt.watermark > conv.meta_read_watermark):
                conv.meta_read_watermark = receipt.watermark
            query = select(Message).where(Message.conversation_id == conv.id, Message.direction == 'outbound',
                Message.role.in_(('agent', 'assistant')),
                Message.kind.in_(('text', 'image', 'document', 'audio', 'video')),
                Message.delivery_status.in_(RANK), Message.channel_message_id.is_not(None))
            query = query.where(Message.channel_message_id.in_(receipt.mids)) if receipt.mids else query.where(
                Message.meta_acknowledged_at <= receipt.watermark)
            messages = db.scalars(query.with_for_update()).all()
            pending = bool(set(receipt.mids) - {message.channel_message_id for message in messages})
            for message in messages:
                # A MID-bearing delivery bounds provider acceptance even if the callback
                # arrived before the HTTP response. Queue creation time is never used.
                if (receipt.mids and receipt.watermark and receipt.kind == 'delivered'
                        and message.dispatch_started_at and receipt.watermark >= message.dispatch_started_at and (
                        message.meta_acknowledged_at is None or receipt.watermark < message.meta_acknowledged_at)):
                    message.meta_acknowledged_at = receipt.watermark
                status = receipt.kind
                if (conv.meta_read_watermark and message.meta_acknowledged_at
                        and message.meta_acknowledged_at <= conv.meta_read_watermark):
                    status = 'read'
                if RANK[status] > RANK[message.delivery_status]:
                    message.delivery_status, message.error, changed = status, None, True
            if not receipt.mids:
                pending = bool(db.scalar(select(Message.id).where(Message.conversation_id == conv.id,
                    Message.direction == 'outbound', Message.delivery_status == 'sending').limit(1)))
        if pending and receipt.received_at > now() - timedelta(minutes=10):
            receipt.next_attempt_at = now() + timedelta(seconds=5)
        else:
            receipt.status = 'ignored' if pending else 'processed'
    if changed:
        publish_sync(tenant_id, 'message.updated', {'conversation_id': identity})
    return True
