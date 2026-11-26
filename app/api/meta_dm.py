"""Verified Meta Instagram/Messenger DM ingestion; durable inbound messages and delivery/read receipts."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import Channel, MetaDmEvent, Session, now, uid
from ..services import meta_media, meta_receipts

router = APIRouter()


def valid_signature(raw: bytes, signature: str | None) -> bool:
    secret = settings.whatsapp_app_secret
    if not secret or not signature or not signature.startswith('sha256='):
        return False
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return secrets.compare_digest(signature, 'sha256=' + digest)


def extract(payload: dict):
    """Yield text/attachment DMs, never echoes or delivery receipts."""
    if payload.get('object') not in ('instagram', 'page'):
        return
    channel = 'instagram' if payload['object'] == 'instagram' else 'messenger'
    entries = payload.get('entry')
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        events = entry.get('messaging')
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            message = event.get('message')
            sender, recipient = event.get('sender'), event.get('recipient')
            if not isinstance(message, dict) or not isinstance(sender, dict) or not isinstance(recipient, dict):
                continue
            mid, body = message.get('mid'), message.get('text')
            parts = message.get('attachments')
            parts = parts if isinstance(parts, list) else []
            sid, rid = sender.get('id'), recipient.get('id')
            if (message.get('is_echo') or not isinstance(mid, str)
                    or not isinstance(sid, str) or not isinstance(rid, str)):
                continue
            if body is None and parts:
                body = ''
            if not isinstance(body, str) or not (body or parts):
                continue
            if not (1 <= len(mid) <= 240 and len(body) <= 4000
                    and 1 <= len(sid) <= 64 and 1 <= len(rid) <= 64):
                continue
            stamp = event.get('timestamp')
            sent_at = None
            if isinstance(stamp, int) and not isinstance(stamp, bool) and 0 < stamp <= int(now().timestamp() * 1000):
                sent_at = datetime.fromtimestamp(stamp / 1000, timezone.utc)
            yield channel, mid, sid, rid, body, sent_at, parts


def store(payload: dict) -> int:
    accepted = 0
    for channel, mid, sender, recipient, body, sent_at, parts in extract(payload):
        with Session() as db:
            owner = db.scalar(select(Channel).where(Channel.kind == channel, Channel.external_id == recipient))
            if owner is None:
                continue
            row = MetaDmEvent(id=uid(), tenant_id=owner.tenant_id, channel=channel,
                              channel_id=owner.id, sent_at=sent_at,
                              provider_message_id=channel + ':' + mid, recipient_id=recipient,
                              sender_id=sender, text=body, status='queued', received_at=now())
            row.attachments = meta_media.snapshot(owner.tenant_id, row.id, parts)
            db.add(row)
            try:
                db.commit()
                accepted += 1
            except IntegrityError:
                db.rollback()  # Meta retries are idempotent across deliveries.
    return accepted


@router.get('/webhooks/meta-dm')
def verify(mode: str = Query('', alias='hub.mode'), token: str = Query('', alias='hub.verify_token'),
           challenge: str = Query('', alias='hub.challenge')):
    expected = settings.whatsapp_verify_token
    if expected and mode == 'subscribe' and secrets.compare_digest(token, expected):
        return PlainTextResponse(challenge)
    raise HTTPException(403)


@router.post('/webhooks/meta-dm')
async def webhook(request: Request):
    raw = await request.body()
    if len(raw) > 1048576:
        raise HTTPException(413, 'Payload too large')
    if not valid_signature(raw, request.headers.get('x-hub-signature-256')):
        raise HTTPException(401, 'Invalid signature')
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, 'Invalid JSON') from None
    if not isinstance(payload, dict):
        raise HTTPException(400, 'Invalid payload')
    await run_in_threadpool(store, payload)
    await run_in_threadpool(meta_receipts.store, payload)
    return {'status': 'ok'}
