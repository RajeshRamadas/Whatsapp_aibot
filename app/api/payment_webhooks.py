"""Opaque connection routing, raw-body HMAC verification, encrypted fourteen-day inbox."""
import hashlib
import hmac
import json
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..db import Session, uid
from ..models.payments import PaymentConnection as Connection
from ..models.payments import PaymentEvent as Event
from ..services.crypto import decrypt, encrypt

router = APIRouter(prefix='/api/v1/payments', tags=['payments'])


@router.post('/webhook/{connection_id}')
async def webhook(connection_id: UUID, request: Request,
                  signature: str = Header('', alias='X-Razorpay-Signature'),
                  event_id: str = Header('', alias='X-Razorpay-Event-Id')):
    with Session() as db:
        c = db.scalar(select(Connection).where(Connection.id == str(connection_id)))
        if not c:
            raise HTTPException(401, 'Invalid webhook signature')
        tenant_id, secret_enc = c.tenant_id, c.webhook_secret_enc
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1048576:
            raise HTTPException(413, 'Webhook payload too large')
    secret = decrypt(tenant_id, secret_enc)
    expected = hmac.new(secret, bytes(raw), hashlib.sha256).hexdigest()
    if len(signature) != 64 or not signature.isascii() or not hmac.compare_digest(expected, signature):
        raise HTTPException(401, 'Invalid webhook signature')
    try:
        body = json.loads(raw)
        event = body['event']
        if not isinstance(event, str) or len(event) > 100:
            raise ValueError()
        payload = body.get('payload', {})
        link = payload.get('payment_link', {}).get('entity', {})
        payment = payload.get('payment', {}).get('entity', {})
        if not isinstance(link, dict) or not isinstance(payment, dict):
            raise ValueError()
    except (TypeError, KeyError, ValueError, AttributeError):
        raise HTTPException(422, 'Invalid webhook payload') from None
    digest = hashlib.sha256((event_id[:200].encode() if event_id else bytes(raw))).hexdigest()
    packet = encrypt(tenant_id, bytes(raw))
    # Persist only reconciliation fields outside the encrypted raw payload. No customer PII is copied here.
    fields = ('id', 'reference_id', 'amount', 'currency', 'amount_paid', 'status', 'notes', 'short_url')
    clean_link = {k: link[k] for k in fields if k in link and k != 'notes'}
    notes = link.get('notes') or {}
    clean_link['notes'] = {k: notes[k] for k in ('workspace', 'request') if isinstance(notes, dict) and k in notes}
    metadata = {'event': event, 'link': clean_link,
                'payment': {k: payment[k] for k in ('id', 'amount', 'currency', 'status') if k in payment}}
    with Session.begin() as db:
        db.execute(insert(Event).values(id=uid(), tenant_id=tenant_id, connection_id=str(connection_id),
            event_key=digest, payload_enc=packet, metadata_json=metadata, state='queued')
            .on_conflict_do_nothing(constraint='uq_payment_event'))
    return {'accepted': True}
