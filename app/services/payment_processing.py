"""Durable merchant payment work; uncertain creates are reconciled, never blindly repeated."""
import random
import re
from datetime import timedelta

from sqlalchemy import and_, or_, select, update

from ..db import Session, Tenant, now
from ..integrations.payment_links import PaymentError, safe_url
from ..models.payments import PaymentConnection as Connection
from ..models.payments import PaymentEvent as Event
from ..models.payments import PaymentRequest as Request
from . import payments
from .appointments import AppointmentError, row
from .audit import audit


def validate(value, remote):
    if (not isinstance(remote, dict) or not re.fullmatch(r'plink_[a-zA-Z0-9]{1,70}', str(remote.get('id', '')))
            or remote.get('reference_id') != value.id or remote.get('amount') != value.amount
            or remote.get('currency') != 'INR' or not isinstance(remote.get('notes'), dict)
            or remote['notes'].get('workspace') != value.tenant_id
            or remote.get('notes', {}).get('request') != value.id
            or value.provider_id and value.provider_id != remote['id']):
        raise PaymentError('payment_details_mismatch')
    if remote.get('status') not in ('created', 'partially_paid', 'paid', 'cancelled', 'expired'):
        raise PaymentError('invalid_response')
    if remote['status'] == 'paid' and remote.get('amount_paid') != value.amount:
        raise PaymentError('payment_details_mismatch')
    if remote['status'] == 'created':
        safe_url(remote.get('short_url'))
    return remote


def apply(db, value, remote, payment_id=None):
    validate(value, remote)
    value.provider_id = remote['id']
    if remote.get('short_url'):
        value.url = safe_url(remote['short_url'])
    if value.state == 'paid':
        return  # Out-of-order cancellation/expiry can never undo verified payment.
    state = remote['status']
    value.state = 'created' if state == 'partially_paid' else state
    if state == 'paid':
        value.payment_id = payment_id
    audit(db, value.tenant_id, 'payments.' + value.state, value.id, actor_type='system')


def execute(tenant_id, identity, stamp):
    with Session() as db:
        value = row(db, Request, tenant_id, identity)
        data = {k: getattr(value, k) for k in ('connection_id', 'submitted', 'provider_id', 'cancel_requested',
            'operation', 'amount', 'description', 'customer_name', 'customer_phone', 'expires_at')}
    if data['cancel_requested'] and not data['submitted'] and not data['provider_id']:
        return None, 'cancelled'
    if not data['submitted'] and not data['provider_id'] and data['expires_at'] <= now():
        return None, 'expired'
    graph = payments.provider(tenant_id, data['connection_id'])
    remote = graph.fetch(data['provider_id']) if data['provider_id'] else graph.find(identity)
    if remote is None:
        if data['submitted']:
            raise PaymentError('creation_unconfirmed', retry=True)
        if data['operation'] != 'create' or data['cancel_requested']:
            return None, 'cancelled' if data['cancel_requested'] else 'approved'
        # Commit uncertainty before POST: a crash after the provider accepted it must never create another link.
        with Session.begin() as db:
            value = row(db, Request, tenant_id, identity, lock=True)
            if value.claimed_at != stamp or value.job_state != 'working':
                raise PaymentError('claim_lost')
            if value.cancel_requested:
                return None, 'cancelled'
            value.submitted = True
        payload = {'amount': data['amount'], 'currency': 'INR', 'accept_partial': False,
            'description': data['description'], 'reference_id': identity,
            'expire_by': int(data['expires_at'].timestamp()),
            'customer': {'name': data['customer_name'], 'contact': data['customer_phone']},
            'notify': {'sms': False, 'email': False}, 'reminder_enable': False,
            'notes': {'workspace': tenant_id, 'request': identity}}
        try:
            remote = graph.create(payload)
        except PaymentError as exc:
            if not exc.uncertain and exc.code in ('provider_rejected', 'credentials_rejected'):
                with Session.begin() as db:
                    value = row(db, Request, tenant_id, identity, lock=True)
                    if value.claimed_at == stamp:
                        value.submitted = False
            raise
    with Session() as db:
        value = row(db, Request, tenant_id, identity)
        validate(value, remote)
        cancel = value.cancel_requested
    if cancel and remote['status'] not in ('paid', 'cancelled', 'expired'):
        remote = graph.cancel(remote['id'])
    return remote, None


def process_one():
    with Session.begin() as db:
        value = db.scalar(select(Request).where(or_(
            and_(Request.job_state == 'queued', Request.next_attempt_at <= now()),
            and_(Request.job_state == 'working', Request.claimed_at < now() - timedelta(minutes=2))))
            .order_by(Request.next_attempt_at, Request.id).with_for_update(skip_locked=True).limit(1))
        if not value:
            return False
        tenant_id, identity, connection_id = value.tenant_id, value.id, value.connection_id
        if db.get(Tenant, tenant_id).suspended:
            value.job_state, value.next_attempt_at = 'queued', now() + timedelta(minutes=5)
            return True
        stamp = now()
        value.job_state, value.claimed_at, value.attempts = 'working', stamp, value.attempts + 1
    error, remote, local_state = None, None, None
    try:
        remote, local_state = execute(tenant_id, identity, stamp)
    except PaymentError as exc:
        error = exc
    except AppointmentError:
        error = PaymentError('connection_unavailable')
    except Exception:
        error = PaymentError('operation_failed')
    with Session.begin() as db:
        value = db.scalar(select(Request).where(Request.id == identity, Request.tenant_id == tenant_id)
                          .with_for_update().execution_options(populate_existing=True))
        if not value or value.claimed_at != stamp or value.job_state != 'working':
            return True
        connection = row(db, Connection, tenant_id, connection_id, lock=True)
        if remote:
            try:
                apply(db, value, remote)
            except PaymentError as exc:
                error = exc
        if error:
            retry = error.retry and value.attempts < 5
            value.job_state, value.last_error = 'queued' if retry else 'failed', error.code
            value.next_attempt_at = now() + timedelta(seconds=max(error.wait,
                30 * 2 ** (value.attempts - 1) + random.uniform(0, 5)))
            if not retry and value.state != 'paid':
                value.state = 'failed'
            connection.last_error = error.code
            if error.code == 'credentials_rejected' and connection.status != 'disconnected':
                connection.status = 'needs_attention'
        else:
            value.job_state, value.last_error = 'complete', None
            if local_state and value.state != 'paid':
                value.state = local_state
            if value.cancel_requested and value.state not in ('paid', 'cancelled', 'expired'):
                # Cancellation arriving during a create is handled by a subsequent durable operation.
                value.state, value.operation, value.job_state = 'cancelling', 'cancel', 'queued'
                value.attempts, value.next_attempt_at = 0, now()
            connection.last_success_at, connection.last_error = now(), None
    return True


def process_event():
    with Session.begin() as db:
        event = db.scalar(select(Event).where(Event.state == 'queued').order_by(Event.created_at, Event.id)
                          .with_for_update(skip_locked=True).limit(1))
        if not event:
            return False
        tenant_id, data = event.tenant_id, event.metadata_json
        remote, payment = data.get('link', {}), data.get('payment', {})
        value = db.scalar(select(Request).where(Request.tenant_id == tenant_id,
            Request.connection_id == event.connection_id, Request.id == remote.get('reference_id')).with_for_update())
        if not value:
            event.state = 'ignored'
            return True
        try:
            expected = {'payment_link.paid': 'paid', 'payment_link.cancelled': 'cancelled',
                        'payment_link.expired': 'expired', 'payment_link.partially_paid': 'partially_paid'
                        }.get(data['event'])
            if expected != remote.get('status'):
                raise PaymentError('payment_details_mismatch')
            payment_id = None
            if expected == 'paid':
                if (payment.get('status') != 'captured' or payment.get('amount') != value.amount
                        or payment.get('currency') != 'INR' or not re.fullmatch(r'pay_[a-zA-Z0-9]{1,70}',
                                                                            str(payment.get('id', '')))):
                    raise PaymentError('payment_details_mismatch')
                payment_id = payment['id']
            apply(db, value, remote, payment_id)
            event.state = 'complete'
        except PaymentError as exc:
            event.state, event.last_error = 'failed', exc.code
    return True


def expire_payloads():
    with Session.begin() as db:
        db.execute(update(Event).where(Event.payload_enc.is_not(None), Event.created_at < now() - timedelta(days=14))
                   .values(payload_enc=None))
