"""Idempotent Google booking saga. Local confirmation follows all remote writes."""
import time
from datetime import datetime, timedelta
from datetime import time as day_time
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select

from ..config import settings
from ..db import Session, Tenant, now
from ..integrations.google_calendar import CalendarError, Client, deadline
from ..models.appointments import Booking
from ..models.google_calendar import GoogleConnection as Connection
from ..models.google_calendar import GoogleTask as Task
from . import google_calendar
from .appointments import booking_view, row


def queue(db, booking, kind='booking'):
    booking.calendar_version += 1
    booking.calendar_state = 'pending' if kind == 'booking' else 'cancelling'
    db.add(Task(tenant_id=booking.tenant_id, kind=kind, resource_id=booking.id,
                revision=booking.calendar_version))


def owned(event, tenant_id, identity):
    return event.get('extendedProperties', {}).get('private', {}) == {'workspace': tenant_id, 'booking': identity}


def event_time(value, zone):
    if 'dateTime' in value:
        at = datetime.fromisoformat(value['dateTime'].replace('Z', '+00:00'))
        if not at.tzinfo:
            raise CalendarError('invalid_response')
        return at
    return datetime.combine(datetime.fromisoformat(value['date']).date(), day_time(), ZoneInfo(zone))


def free(provider, token, target, start, end, excluded, tenant_id):
    busy = provider.freebusy(token, [target['calendar_id']], start, end)[target['calendar_id']]
    if not busy:
        return
    page = None
    for _ in range(8):
        data = provider.request('GET', provider.path(target['calendar_id']), token=token, params={
            'timeMin': start.isoformat(), 'timeMax': end.isoformat(), 'singleEvents': 'true', 'maxResults': 250,
            **({'pageToken': page} if page else {})})
        for event in data.get('items', []):
            if event.get('status') == 'cancelled' or event.get('transparency') == 'transparent':
                continue
            if event.get('id') in excluded and owned(event, tenant_id, excluded[event['id']]):
                continue
            try:
                first = event_time(event['start'], target['timezone'])
                last = event_time(event['end'], target['timezone'])
            except (KeyError, ValueError, TypeError):
                raise CalendarError('invalid_response') from None
            if first < end and last > start:
                raise CalendarError('calendar_busy')
        page = data.get('nextPageToken')
        if not page:
            return
    raise CalendarError('availability_unavailable', retry=True)


def snapshot(tenant_id, identity):
    with Session() as db:
        booking = row(db, Booking, tenant_id, identity)
        previous = row(db, Booking, tenant_id, booking.replaces_id) if booking.replaces_id else None
        return (booking_view(db, booking), list(booking.calendar_targets),
                list(previous.calendar_targets) if previous else [])


def execute(tenant_id, identity, kind):
    value, targets, previous = snapshot(tenant_id, identity)
    for target in targets:
        provider = Client(tenant_id)
        token = google_calendar.access(tenant_id, target['connection_id'])
        path = provider.path(target['calendar_id'], target['event_id'])
        event = provider.request('GET', path, token=token, missing=True)
        if event and not owned(event, tenant_id, identity):
            raise CalendarError('event_ownership_changed')
        if kind == 'calendar_cancel':
            if event:
                provider.request('DELETE', path, token=token, missing=True, params={'sendUpdates': 'none'},
                                 headers={'If-Match': event['etag']})
            continue
        if event:
            continue  # Deterministic event IDs make retries and uncertain insert outcomes idempotent.
        with Session() as db:
            booking = row(db, Booking, tenant_id, identity)
            if booking.state != 'confirming':
                return
        excluded = {t['event_id']: value['replaces_id'] for t in previous if t['calendar_id'] == target['calendar_id']}
        free(provider, token, target, value['starts_at'], value['ends_at'], excluded, tenant_id)
        link = settings.public_app_url.rstrip('/') + '/dashboard/inbox?conversation=' + value['conversation_id']
        payload = {'id': target['event_id'], 'summary': value['service'] + ' · ' + value['customer_name'],
            'description': value['customer_name'] + '\n' + value['customer_phone'] + '\n' + link,
            'start': {'dateTime': value['starts_at'].isoformat()}, 'end': {'dateTime': value['ends_at'].isoformat()},
            'extendedProperties': {'private': {'workspace': tenant_id, 'booking': identity}}}
        try:
            provider.request('POST', provider.path(target['calendar_id']), token=token,
                             payload=payload, params={'sendUpdates': 'none'})
        except CalendarError as exc:
            if exc.code != 'event_conflict':
                raise
            existing = provider.request('GET', path, token=token)
            if not owned(existing, tenant_id, identity):
                raise CalendarError('event_ownership_changed') from None


def process_one():
    with Session.begin() as db:
        task = db.scalar(select(Task).where(Task.kind.in_(['booking', 'calendar_cancel']), or_(
            and_(Task.state == 'queued', Task.next_attempt_at <= now()),
            and_(Task.state == 'working', Task.claimed_at < now() - timedelta(minutes=3))))
            .order_by(Task.next_attempt_at, Task.id).with_for_update(skip_locked=True).limit(1))
        if not task:
            return False
        tenant_id, identity, task_id, version, kind = (
            task.tenant_id, task.resource_id, task.id, task.revision, task.kind)
        tenant = db.get(Tenant, tenant_id, with_for_update=True)
        booking = row(db, Booking, tenant_id, identity)
        if booking.calendar_version != version:
            task.state = 'complete'
            return True
        if tenant.suspended:
            task.next_attempt_at = now() + timedelta(minutes=5)
            return True
        connections = []
        for connection_id in sorted({t['connection_id'] for t in booking.calendar_targets}):
            c = row(db, Connection, tenant_id, connection_id, lock=True)
            if c.token_lease_until and c.token_lease_until > now():
                task.next_attempt_at = c.token_lease_until
                return True
            connections.append(c)
        expired = kind == 'booking' and task.created_at < now() - timedelta(minutes=10)
        stamp = now()
        for c in connections:
            c.token_lease_until = stamp + timedelta(minutes=3)
        task.state, task.claimed_at, task.attempts = 'working', stamp, task.attempts + 1
    error, context = None, deadline.set(time.monotonic() + 8)
    try:
        if expired:
            raise CalendarError('confirmation_expired')
        execute(tenant_id, identity, kind)
    except CalendarError as exc:
        error = exc
    except Exception:
        error = CalendarError('operation_failed')
    finally:
        deadline.reset(context)
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        task = row(db, Task, tenant_id, task_id, lock=True)
        booking = row(db, Booking, tenant_id, identity, lock=True)
        if task.claimed_at != stamp:
            return True
        for target in booking.calendar_targets:
            c = row(db, Connection, tenant_id, target['connection_id'], lock=True)
            if c.token_lease_until == stamp + timedelta(minutes=3):
                c.token_lease_until = None
            c.busy = {}  # Refresh availability after writes, including uncertain outcomes.
        if booking.calendar_version != version:
            task.state = 'complete'
            if kind == 'booking':
                queue(db, booking, 'calendar_cancel')  # Catch a create racing a cancellation.
            return True
        if error:
            retry = error.retry and task.attempts < 5 and task.created_at > now() - timedelta(minutes=10)
            if error.code == 'work_pending' and task.created_at > now() - timedelta(minutes=10):
                retry, task.attempts = True, task.attempts - 1
            task.state, task.last_error = 'queued' if retry else 'failed', error.code
            task.next_attempt_at = now() + timedelta(seconds=max(error.wait, 30 * 2 ** (max(task.attempts, 1) - 1)))
            booking.calendar_error = error.code
            if retry:
                return True
            booking.calendar_state = 'failed'
            if kind == 'booking':
                booking.state, booking.reminder_state = 'failed', 'skipped'
                queue(db, booking, 'calendar_cancel')
                booking.calendar_state = 'failed'
        else:
            task.state, booking.calendar_error = 'complete', None
            booking.calendar_state = 'synced' if kind == 'booking' else 'cancelled'
            if kind == 'booking':
                if booking.replaces_id:
                    previous = row(db, Booking, tenant_id, booking.replaces_id, lock=True)
                    if previous.state != 'confirmed':
                        booking.state, booking.calendar_state = 'failed', 'failed'
                        queue(db, booking, 'calendar_cancel')
                        return True
                    previous.state, previous.reminder_state = 'cancelled', 'cancelled'
                    if previous.calendar_targets:
                        queue(db, previous, 'calendar_cancel')
                booking.state = 'confirmed'
                booking.reminder_state = 'pending' if booking.reminder_opt_in else 'skipped'
        if kind == 'booking':
            from .calendar_notifications import stage
            stage(db, booking)
    return True
