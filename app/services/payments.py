"""Approved payment amounts and encrypted merchant connections; no provider calls in mutations."""
import json
from datetime import timedelta

from sqlalchemy import select

from ..db import Channel, Conversation, Message, Session, Template, Tenant, now
from ..integrations.payment_links import PaymentError, Razorpay
from ..models.appointments import Booking
from ..models.payments import PaymentConnection as Connection
from ..models.payments import PaymentRequest as Request
from . import crypto
from .appointments import AppointmentError, row
from .audit import audit
from .customer_languages import normalize
from .templates import variable_count


def connection_view(c):
    return {k: getattr(c, k) for k in ('id', 'label', 'mode', 'key_last4', 'status', 'receipt_template_id',
                                      'last_success_at', 'last_error')}


def connections(tenant_id):
    with Session() as db:
        return [connection_view(c) for c in db.scalars(select(Connection).where(Connection.tenant_id == tenant_id)
                                                       .order_by(Connection.created_at.desc()).limit(20))]


def connect(tenant_id, values, actor=('system', None)):
    key, secret, webhook = (values[k] for k in ('key_id', 'key_secret', 'webhook_secret'))
    mode = 'live' if key.startswith('rzp_live_') else 'test'
    if mode == 'live' and not values['confirm_live']:
        raise AppointmentError('Explicit live-mode approval is required', 422)
    packet = crypto.encrypt(tenant_id, json.dumps({'key_id': key, 'key_secret': secret}))
    webhook_packet = crypto.encrypt(tenant_id, webhook)
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        if db.scalar(select(Connection.id).where(Connection.tenant_id == tenant_id,
                                                  Connection.status != 'disconnected').limit(1)):
            raise AppointmentError('Disconnect the current merchant account first')
        template_id = values.get('receipt_template_id')
        if template_id:
            template = db.scalar(select(Template).join(Channel).where(Channel.tenant_id == tenant_id,
                Template.id == template_id, Template.status == 'APPROVED', Template.category == 'UTILITY'))
            if not template or not template.body or variable_count(template.body) != 3:
                raise AppointmentError('Choose an approved utility receipt template with three variables', 422)
        c = Connection(tenant_id=tenant_id, label=values['label'], mode=mode, key_last4=key[-4:],
            credentials_enc=packet, webhook_secret_enc=webhook_packet, receipt_template_id=template_id)
        db.add(c)
        db.flush()
        audit(db, tenant_id, 'payments.connected', c.id, actor_type=actor[0], actor_user_id=actor[1])
        return connection_view(c)


def provider(tenant_id, identity, *, require_connected=False):
    with Session() as db:
        c = row(db, Connection, tenant_id, identity)
        if not c.credentials_enc or c.status == 'disconnected' or require_connected and c.status != 'connected':
            raise AppointmentError('Test and connect the merchant account first')
        packet = c.credentials_enc
    return Razorpay(tenant_id, json.loads(crypto.decrypt(tenant_id, packet)))


def test_connection(tenant_id, identity, actor=('system', None)):
    graph = provider(tenant_id, identity)
    error = None
    try:
        graph.test()
    except PaymentError as exc:
        error = exc.code
    with Session.begin() as db:
        c = row(db, Connection, tenant_id, identity, lock=True)
        if c.status == 'disconnected':
            return connection_view(c)
        c.status, c.last_error = ('needs_attention', error) if error else ('connected', None)
        if not error:
            c.last_success_at = now()
        audit(db, tenant_id, 'payments.tested', c.id, details={'success': not bool(error)},
              actor_type=actor[0], actor_user_id=actor[1])
        return connection_view(c)


def disconnect(tenant_id, identity, actor=('system', None)):
    with Session.begin() as db:
        c = row(db, Connection, tenant_id, identity, lock=True)
        c.status, c.credentials_enc = 'disconnected', None
        audit(db, tenant_id, 'payments.disconnected', identity, actor_type=actor[0], actor_user_id=actor[1])
        return connection_view(c)


def view(db, value):
    result = {k: getattr(value, k) for k in ('id', 'connection_id', 'conversation_id', 'booking_id', 'amount',
        'description', 'language', 'expires_at', 'state', 'provider_id', 'url', 'payment_id', 'last_error',
        'job_state')}
    for name in ('link', 'receipt'):
        message_id = getattr(value, name + '_message_id')
        message = db.scalar(select(Message).join(Conversation).where(Message.id == message_id,
            Conversation.tenant_id == value.tenant_id,
            Message.conversation_id == value.conversation_id)) if message_id else None
        result[name + '_delivery'] = message.delivery_status if message else None
    return result


def listing(tenant_id, conversation_id=None):
    with Session() as db:
        if conversation_id:
            row(db, Conversation, tenant_id, conversation_id)
        query = select(Request).where(Request.tenant_id == tenant_id)
        if conversation_id:
            query = query.where(Request.conversation_id == conversation_id)
        return [view(db, value) for value in db.scalars(
            query.order_by(Request.created_at.desc(), Request.id).limit(100))]


def approve(tenant_id, values, actor=('system', None)):
    if not now() + timedelta(minutes=5) < values['expires_at'] <= now() + timedelta(days=30):
        raise AppointmentError('Choose an expiry between five minutes and thirty days from now', 422)
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        c = row(db, Connection, tenant_id, values['connection_id'])
        if c.status != 'connected':
            raise AppointmentError('Test and connect the merchant account first')
        conversation = row(db, Conversation, tenant_id, values['conversation_id'])
        phone = conversation.external_user if conversation.channel == 'whatsapp' else values['customer_phone']
        values = {**values, 'customer_phone': phone, 'language': normalize(values.get('language') or
                    db.get(Tenant, tenant_id).default_language)}
        if values.get('booking_id'):
            b = row(db, Booking, tenant_id, values['booking_id'])
            if b.conversation_id != conversation.id:
                raise AppointmentError('Booking not found', 404)
            if b.state in ('cancelled', 'expired', 'failed'):
                raise AppointmentError('Choose an active booking')
        old = db.scalar(select(Request).where(Request.tenant_id == tenant_id,
                                              Request.approval_key == values['approval_key']))
        if old:
            if any(getattr(old, k) != v for k, v in values.items()):
                raise AppointmentError('This approval key was already used with different details')
            return view(db, old)
        value = Request(tenant_id=tenant_id, **values)
        db.add(value)
        db.flush()
        audit(db, tenant_id, 'payments.approved', value.id, details={'amount_paise': value.amount},
              actor_type=actor[0], actor_user_id=actor[1])
        return view(db, value)


def action(tenant_id, identity, kind, conversation_id=None, actor=('system', None)):
    with Session.begin() as db:
        value = row(db, Request, tenant_id, identity, lock=True)
        if conversation_id and value.conversation_id != conversation_id:
            raise AppointmentError('Payment request not found', 404)
        if kind == 'cancel' and value.state != 'paid':
            value.cancel_requested, value.state = True, 'cancelling'
        if value.state == 'paid' or value.job_state in ('queued', 'working'):
            return view(db, value)
        if kind == 'send' and value.state == 'created':
            return view(db, value)
        if kind == 'send' and value.expires_at <= now():
            raise AppointmentError('This payment request has expired')
        if kind not in ('send', 'sync', 'cancel'):
            raise AppointmentError('Unknown payment action', 422)
        if kind == 'send':
            if value.state not in ('approved', 'failed'):
                raise AppointmentError('This payment request cannot be sent')
            row(db, Conversation, tenant_id, value.conversation_id)
        c = row(db, Connection, tenant_id, value.connection_id)
        if c.status != 'connected':
            raise AppointmentError('Test and connect the merchant account first')
        value.operation = {'send': 'create', 'sync': 'sync', 'cancel': 'cancel'}[kind]
        value.job_state, value.attempts, value.next_attempt_at, value.last_error = 'queued', 0, now(), None
        if kind == 'send':
            value.state = 'queued'
        audit(db, tenant_id, 'payments.' + kind, identity, actor_type=actor[0], actor_user_id=actor[1])
        return view(db, value)
