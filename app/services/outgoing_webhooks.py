"""Database-only event fan-out; mutations and their outbox rows commit together."""
import json
from datetime import timedelta
from typing import Literal, get_args

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..db import now, uid
from ..models.webhooks import WebhookDelivery, WebhookEndpoint

EventType = Literal['message.received', 'message.sent', 'conversation.handoff', 'conversation.closed',
                    'lead.created', 'lead.updated', 'booking.created', 'payment.completed', 'campaign.completed']
EVENTS = frozenset(get_args(EventType))


def stage(db, tenant_id, kind, event_key, data):
    if kind not in EVENTS:
        raise ValueError('Unsupported outgoing event')
    endpoints = db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id,
        WebhookEndpoint.enabled, WebhookEndpoint.events.contains([kind])).with_for_update(read=True)).all()
    identity, stamp = 'evt_' + uid(), now()
    payload = {'id': identity, 'type': kind, 'created_at': stamp.isoformat(), 'tenant_id': tenant_id, 'data': data}
    # Payloads intentionally contain IDs/statuses, never message text, names, numbers or secrets.
    if len(json.dumps(payload).encode()) > 16384:
        raise ValueError('Outgoing event exceeds metadata limit')
    for endpoint in endpoints:
        db.execute(insert(WebhookDelivery).values(id=uid(), tenant_id=tenant_id, endpoint_id=endpoint.id,
            event_key=event_key, payload=payload, state='queued', attempts=0, history=[],
            expires_at=stamp + timedelta(hours=24), created_at=stamp, next_attempt_at=stamp)
            .on_conflict_do_nothing(constraint='uq_webhook_delivery_event'))
