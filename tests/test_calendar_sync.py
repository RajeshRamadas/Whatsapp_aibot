"""Google event sagas: final confirmation, retries, external conflicts and cancel races."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import Message, Session, engine, now
from app.integrations.google_calendar import CalendarError, Client
from app.models.appointments import Booking
from app.models.google_calendar import GoogleConnection as Connection
from app.models.google_calendar import GoogleTask as Task
from app.services import appointments, calendar_sync, google_jobs
from tests.test_appointments import setup as setup
from tests.test_calendar_availability import attach
from tests.test_google_calendar import connected as connected


@pytest.fixture
def calendar(client, tenant, connected, setup, monkeypatch):
    headers, _, connection, body, _ = attach(client, tenant, connected, setup)
    events, busy = {}, []
    monkeypatch.setattr(Client, 'freebusy', lambda self, token, ids, start, end: {i: busy[:] for i in ids})
    assert google_jobs.process_one()
    original = Client.request
    def request(self, method, path, **kwargs):
        assert engine.pool.checkedout() == 0
        if '/events' not in path:
            return original(self, method, path, **kwargs)
        identity = path.rsplit('/', 1)[-1]
        if method == 'POST':
            event = {**kwargs['payload'], 'etag': '"v1"'}
            events[event['id']] = event
            return event
        if method == 'DELETE':
            assert kwargs['headers']['If-Match'] == '"v1"'
            return events.pop(identity, None)
        if identity == 'events':
            return {'items': list(events.values()) + [{'id': 'external', 'start': {'dateTime': b['start']},
                        'end': {'dateTime': b['end']}} for b in busy]}
        return events.get(identity)
    monkeypatch.setattr(Client, 'request', request)
    return headers, events, busy, connection['id']


def hold_confirm(client, setup, headers):
    hold = client.post('/api/v1/appointments/bookings', headers=headers, json=setup[5]).json()
    result = client.post('/api/v1/appointments/bookings/' + hold['id'] + '/confirm',
                          headers=headers, json={'confirm': True})
    assert result.status_code == 200 and result.json()['state'] == 'confirming', result.text
    return hold['id']


def refresh_cache(tenant_id, identity):
    from app.services.calendar_availability import fetch
    result = fetch(tenant_id, identity, None)
    with Session.begin() as db:
        c = db.scalar(select(Connection).where(Connection.id == identity, Connection.tenant_id == tenant_id))
        c.busy = result['busy']


def test_confirmation_reschedule_cancel_idempotency(client, tenant, setup, calendar):
    headers, events, busy, connection_id = calendar
    identity = hold_confirm(client, setup, headers)
    assert not events and calendar_sync.process_one()
    assert len(events) == 1
    event = next(iter(events.values()))
    assert setup[5]['customer_phone'] in event['description'] and 'conversation=' in event['description']
    with Session.begin() as db:
        b = db.get(Booking, identity)
        assert b.state == 'confirmed' and b.calendar_state == 'synced'
        assert db.scalar(select(Message).where(Message.conversation_id == b.conversation_id,
                                               Message.reply_key.startswith('calendar:'))).role == 'agent'
        task = db.scalar(select(Task).where(Task.resource_id == identity, Task.kind == 'booking'))
        task.state, task.next_attempt_at = 'queued', now()
    assert calendar_sync.process_one() and len(events) == 1
    refresh_cache(tenant['tenant_id'], connection_id)
    slot = client.post('/api/v1/appointments/slots', headers=headers,
        json={**setup[3], 'starts_at': (now() + timedelta(days=4)).isoformat()}).json()
    held = client.post('/api/v1/appointments/bookings', headers=headers,
        json={**setup[5], 'slot_id': slot['id'], 'replaces_id': identity}).json()
    client.post('/api/v1/appointments/bookings/' + held['id'] + '/confirm', headers=headers, json={'confirm': True})
    with Session() as db:
        assert db.get(Booking, identity).state == 'confirmed'
    assert calendar_sync.process_one() and len(events) == 2
    assert calendar_sync.process_one() and len(events) == 1
    client.post('/api/v1/appointments/bookings/' + held['id'] + '/cancel', headers=headers, json={'confirm': True})
    assert calendar_sync.process_one() and not events


def test_external_busy_recheck_preserves_original(client, tenant, setup, calendar):
    headers, events, busy, _ = calendar
    identity = hold_confirm(client, setup, headers)
    busy.append({'start': setup[4]['starts_at'], 'end': setup[4]['ends_at']})
    assert calendar_sync.process_one() and not events
    with Session() as db:
        booking = db.get(Booking, identity)
        assert booking.state == 'failed' and booking.calendar_error == 'calendar_busy'
        assert db.scalar(select(Task).where(Task.resource_id == identity, Task.kind == 'booking')).state == 'failed'
    assert calendar_sync.process_one() and not events


def test_uncertain_create_retry_and_cancel_race(client, tenant, setup, calendar, monkeypatch):
    headers, events, busy, _ = calendar
    identity = hold_confirm(client, setup, headers)
    original, fail = Client.request, [True]
    def uncertain(self, method, path, **kwargs):
        result = original(self, method, path, **kwargs)
        if method == 'POST' and fail.pop():
            raise CalendarError('provider_unavailable', retry=True)
        return result
    monkeypatch.setattr(Client, 'request', uncertain)
    assert calendar_sync.process_one() and len(events) == 1
    with Session.begin() as db:
        task = db.scalar(select(Task).where(Task.resource_id == identity, Task.kind == 'booking'))
        task.next_attempt_at = now()
    assert calendar_sync.process_one() and len(events) == 1
    with Session() as db:
        assert db.get(Booking, identity).state == 'confirmed'
    appointments.transition(tenant['tenant_id'], identity, 'cancel')
    assert calendar_sync.process_one() and not events
