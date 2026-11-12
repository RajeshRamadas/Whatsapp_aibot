"""Only server-bound WhatsApp identity or a literal email code releases an order."""
import asyncio
import hmac
import json
import re
import secrets
from datetime import timedelta

from sqlalchemy import delete, func, select, update

from ..config import settings
from ..db import Conversation, Session, Tenant, now, uid
from ..integrations.http_actions.transport import ActionError
from ..models.order_proofs import OrderProof
from ..rate_limits import RateLimiter
from . import commerce, crypto, email
from .email_templates import normalize
from .http_actions import code_hash, digest, phone


def conversation(db, tenant_id, identity):
    conv = db.scalar(select(Conversation).where(Conversation.tenant_id == tenant_id, Conversation.id == identity))
    if (not conv or conv.handoff or conv.status != 'bot' or db.get(Tenant, tenant_id).suspended):
        raise ActionError('conversation_unavailable')
    return conv


def active(db, tenant_id, identity, *, lock=False):
    row = commerce.scoped(db, tenant_id, identity, lock=lock)
    if row.status != 'connected':
        raise ActionError('store_unavailable')
    return row


def pending(row):
    if row.used or row.expires_at <= now():
        return {'state': 'unavailable'}
    return {'state': 'awaiting_code', 'reference': row.id.replace('-', '')[:12]}


def delivery_enabled():
    return settings.email_backend == 'ses'


def lookup(tenant_id, store_id, conversation_id, turn_key, order_number, supplied_phone=None):
    # supplied_phone is deliberately ignored: a model argument is not identity proof.
    key = digest([store_id, conversation_id, turn_key, order_number])
    with Session() as db:
        row = active(db, tenant_id, store_id)
        conv = conversation(db, tenant_id, conversation_id)
        previous = db.scalar(select(OrderProof).where(OrderProof.tenant_id == tenant_id, OrderProof.dedupe_key == key))
        if previous:
            return pending(previous)
    try:
        if (not RateLimiter(30, namespace='store-orders').allow(tenant_id) or
                not RateLimiter(5, namespace='store-customer-orders').allow(tenant_id + ':' + conversation_id)):
            raise ActionError('rate_limited')
        packet = asyncio.run(commerce.provider(row).order(order_number))
        if not packet:
            return {'state': 'unavailable'}
        data = {k: v for k, v in packet.items() if k not in ('phones', 'email')}
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(encoded) > 16000:
            raise ActionError('order_too_large')
        customer_phone = phone(conv.external_user) if conv.channel == 'whatsapp' else None
        # Accept formatting separators, but never infer a country code for local numbers.
        order_phones = [phone(re.sub(r'[\s().-]', '', str(p or ''))) for p in packet.get('phones', [])]
        if customer_phone and any(p and hmac.compare_digest(p, customer_phone) for p in order_phones):
            with Session() as db:
                active(db, tenant_id, store_id)
                current = conversation(db, tenant_id, conversation_id)
                if current.channel != conv.channel or current.external_user != conv.external_user:
                    raise ActionError('identity_changed')
            return {'state': 'complete', 'data': data}
        if not delivery_enabled():
            return {'state': 'unavailable'}
        recipient = normalize(packet.get('email') or '')
        if not RateLimiter(3, 300, namespace='store-order-codes').allow(tenant_id + ':' + digest(recipient)):
            raise ActionError('rate_limited')
        identity, code = uid(), f'{secrets.randbelow(1000000):06d}'
        encrypted = crypto.encrypt(tenant_id, encoded.encode())
        with Session.begin() as db:
            active(db, tenant_id, store_id, lock=True)
            conversation(db, tenant_id, conversation_id)
            previous = db.scalar(select(OrderProof).where(OrderProof.tenant_id == tenant_id,
                OrderProof.dedupe_key == key))
            if previous:
                return pending(previous)
            proof = OrderProof(id=identity, tenant_id=tenant_id, connection_id=store_id,
                conversation_id=conversation_id, dedupe_key=key, payload_enc=encrypted,
                code_hash=code_hash('order:' + identity, code), expires_at=now() + timedelta(minutes=5))
            db.add(proof)
            email.sender.stage(db, recipient, 'Verify order lookup', code)
            db.flush()
            return pending(proof)
    except Exception:
        # Never expose provider diagnostics, recipient addresses or order existence.
        return {'state': 'unavailable'}


def verify(tenant_id, conversation_id, reference, code):
    if not re.fullmatch(r'[a-f0-9]{12}', reference) or not re.fullmatch(r'[0-9]{6}', code):
        return {'state': 'unavailable'}
    with Session() as db:
        snapshot = db.scalar(select(OrderProof).where(OrderProof.tenant_id == tenant_id,
            OrderProof.conversation_id == conversation_id,
            func.substr(func.replace(OrderProof.id, '-', ''), 1, 12) == reference))
    if not snapshot:
        return {'state': 'unavailable'}
    # Decode before the locked transaction; tenant secret IO never nests a pool session.
    try:
        data = json.loads(crypto.decrypt(tenant_id, snapshot.payload_enc)) if snapshot.payload_enc else None
        with Session.begin() as db:
            active(db, tenant_id, snapshot.connection_id, lock=True)
            conversation(db, tenant_id, conversation_id)
            proof = db.scalar(select(OrderProof).where(OrderProof.tenant_id == tenant_id,
                OrderProof.id == snapshot.id, OrderProof.conversation_id == conversation_id).with_for_update())
            if not proof or proof.used or proof.expires_at <= now() or not data:
                return {'state': 'unavailable'}
            proof.attempts += 1
            valid = hmac.compare_digest(proof.code_hash or '', code_hash('order:' + proof.id, code))
            if valid or proof.attempts >= 5:
                proof.used, proof.code_hash, proof.payload_enc = True, None, None
            if not valid:
                return {'state': 'unavailable'}
            return {'state': 'complete', 'data': data}
    except Exception:
        return {'state': 'unavailable'}


def maintain():
    with Session.begin() as db:
        db.execute(update(OrderProof).where(OrderProof.expires_at <= now(), OrderProof.payload_enc.is_not(None))
                   .values(payload_enc=None, code_hash=None, used=True))
        db.execute(delete(OrderProof).where(OrderProof.expires_at < now() - timedelta(days=1)))
