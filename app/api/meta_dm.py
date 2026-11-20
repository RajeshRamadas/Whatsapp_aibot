"""Verified Meta Instagram/Messenger DM ingestion; no outbound messages in this phase."""
import hashlib
import hmac
import json
import secrets

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import Channel, MetaDmEvent, Session, now, uid

router = APIRouter()


def valid_signature(raw: bytes, signature: str | None) -> bool:
    secret = settings.whatsapp_app_secret
    if not secret or not signature or not signature.startswith('sha256='):
        return False
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return secrets.compare_digest(signature, 'sha256=' + digest)


def extract(payload: dict):
    """Yield only supported text DMs, never echoes, delivery receipts or unknown events."""
    if payload.get('object') not in ('instagram', 'page'):
        return
    channel = 'instagram' if payload['object'] == 'instagram' else 'messenger'
    for entry in payload.get('entry', []):
        if not isinstance(entry, dict):
            continue
        for event in entry.get('messaging', []):
            if not isinstance(event, dict):
                continue
            message = event.get('message')
            sender, recipient = event.get('sender'), event.get('recipient')
            if not isinstance(message, dict) or not isinstance(sender, dict) or not isinstance(recipient, dict):
                continue
            mid, body = message.get('mid'), message.get('text')
            sid, rid = sender.get('id'), recipient.get('id')
            if (message.get('is_echo') or not isinstance(mid, str) or not isinstance(body, str)
                    or not isinstance(sid, str) or not isinstance(rid, str)):
                continue
            if not (1 <= len(mid) <= 240 and 1 <= len(body) <= 4000
                    and 1 <= len(sid) <= 64 and 1 <= len(rid) <= 64):
                continue
            yield channel, mid, sid, rid, body


def store(payload: dict) -> int:
    accepted = 0
    for channel, mid, sender, recipient, body in extract(payload):
        with Session() as db:
            owner = db.scalar(select(Channel).where(Channel.kind == channel, Channel.external_id == recipient))
            if owner is None:
                continue
            row = MetaDmEvent(id=uid(), tenant_id=owner.tenant_id, channel=channel,
                              provider_message_id=channel + ':' + mid, recipient_id=recipient,
                              sender_id=sender, text=body, status='queued', received_at=now())
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
    return {'status': 'ok'}
