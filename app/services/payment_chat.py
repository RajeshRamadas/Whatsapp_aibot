"""Conversation-bound tools only send agent-approved amounts; transactional copy is deterministic."""
import json
import time
from pathlib import Path

from sqlalchemy import select

from .. import llm
from ..db import Session
from ..models.payments import PaymentRequest as Request
from . import payments
from .appointments import AppointmentError
from .audit import audit
from .customer_languages import LANGUAGES, normalize

COPY = {code: json.loads((Path(__file__).parents[1] / 'messages' / 'payments' / f'{code}.json').read_text())
        for code in LANGUAGES}


def amount(value):
    return f'{value // 100}.{value % 100:02d}'


def notice(tenant, value, kind=None):
    kind = kind or ('paid' if value['state'] == 'paid' else 'link' if value['state'] == 'created' else 'queued')
    from .appointment_chat import local_time
    return COPY[normalize(value['language'])][kind].format(**{
        **value, 'amount': amount(value['amount']), 'expires': local_time(tenant, value['expires_at']),
        'reference': value.get('payment_id') or value.get('provider_id') or value['id']})


def tools(tenant, conversation_id):
    result = {}
    if not conversation_id:
        return [], result
    with Session() as db:
        enabled = db.scalar(select(Request.id).where(Request.tenant_id == tenant.id,
            Request.conversation_id == conversation_id).limit(1))
    if not enabled:
        return [], result

    def logged(name, handler):
        def call(args):
            start, status = time.monotonic(), 'failed'
            try:
                response = handler(args)
                status = 'success'
                return response
            finally:
                with Session.begin() as db:
                    audit(db, tenant.id, 'payments.tool', details={'tool': name, 'status': status,
                        'request_id': args.get('request_id'), 'duration_ms': int((time.monotonic() - start) * 1000)},
                        actor_type='bot')
        return call

    def mine(args):
        return [{'id': v['id'], 'description': v['description'], 'amount_inr': amount(v['amount']), 'state': v['state'],
                 'expires_at': v['expires_at'].isoformat()}
                for v in payments.listing(tenant.id, conversation_id)]

    def send(args):
        if result:
            raise AppointmentError('Only one payment request may be sent per turn')
        value = payments.action(tenant.id, args['request_id'], 'send', conversation_id=conversation_id,
                                actor=('bot', None))
        if value['state'] not in ('queued', 'created', 'paid'):
            raise AppointmentError('The team must review this payment request')
        result.update(value)
        return {'state': value['state'], 'instructions': notice(tenant, value)}

    return [llm.Tool('my_payment_requests',
            'List only this customer’s agent-approved dues and verified payment states.',
            {'type': 'object', 'additionalProperties': False}, logged('my_payment_requests', mine)),
        llm.Tool('send_payment_link', 'Send one approved payment link when the customer asks to pay. '
                 'Use only a request ID returned by my_payment_requests; amounts cannot be changed.',
                 {'type': 'object', 'required': ['request_id'], 'additionalProperties': False,
                  'properties': {'request_id': {'type': 'string', 'format': 'uuid', 'minLength': 36, 'maxLength': 36}}},
                 logged('send_payment_link', send))], result
