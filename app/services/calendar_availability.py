"""Cached Google availability plus tenant hours and atomic local reservations."""
import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update

from ..db import Session, Tenant, now
from ..integrations.google_calendar import Client
from ..models.appointments import Booking, Service, Slot, Staff
from ..models.google_calendar import CalendarBinding as Binding
from ..models.google_calendar import GoogleConnection as Connection
from ..models.google_calendar import GoogleTask as Task
from .appointments import AppointmentError, active_booking, row
from .audit import audit
from .bot_policy import validate_hours
from .google_calendar import access, enqueue


def bindings(tenant_id):
    with Session() as db:
        return [{k: getattr(b, k) for k in ('id', 'connection_id', 'calendar_id', 'service_id', 'staff_id')}
                for b in db.scalars(select(Binding).where(Binding.tenant_id == tenant_id).order_by(Binding.id))]


def bind(tenant_id, values):
    values = {k: v for k, v in values.items() if k != 'confirm_data_sharing'}
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        connection = row(db, Connection, tenant_id, values['connection_id'])
        if connection.status != 'connected' or values['calendar_id'] not in {c['id'] for c in connection.calendars}:
            raise AppointmentError('Connect a writable calendar first', 422)
        for model, key in ((Service, 'service_id'), (Staff, 'staff_id')):
            if values.get(key):
                row(db, model, tenant_id, values[key])
        current = db.scalars(select(Binding).where(Binding.tenant_id == tenant_id)).all()
        if len(current) >= 5:
            raise AppointmentError('At most five calendar bindings are supported')
        if any(all(getattr(b, k) == v for k, v in values.items()) for b in current):
            raise AppointmentError('This calendar binding already exists')
        binding = Binding(tenant_id=tenant_id, **values)
        db.add(binding)
        db.flush()
        enqueue(db, connection, 'availability')
        audit(db, tenant_id, 'google_calendar.binding_created', binding.id)
        return binding.id


def unbind(tenant_id, identity):
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        db.delete(row(db, Binding, tenant_id, identity))
        audit(db, tenant_id, 'google_calendar.binding_removed', identity)


def targets(db, tenant_id, service_id, staff_id):
    rows = db.execute(select(Binding, Connection).join(Connection, Connection.id == Binding.connection_id).where(
        Binding.tenant_id == tenant_id, Connection.tenant_id == tenant_id,
        or_(Binding.service_id.is_(None), Binding.service_id == service_id),
        or_(Binding.staff_id.is_(None), Binding.staff_id == staff_id))).all()
    return list({b.calendar_id: (b, c) for b, c in rows}.values())


def in_hours(tenant, start, end):
    zone = ZoneInfo(tenant.timezone)
    first, last = start.astimezone(zone), end.astimezone(zone)
    hours = validate_hours(tenant.business_hours)
    if not hours:
        return True
    window = hours.get(str(first.weekday()))
    return bool(window and first.date() == last.date()
                and window[0] <= first.strftime('%H:%M') and last.strftime('%H:%M') <= window[1])


def signature(tenant, service, pairs):
    return hashlib.sha256(json.dumps([tenant.timezone, tenant.business_hours, service.duration_minutes,
        sorted((b.connection_id, b.calendar_id) for b, c in pairs)]).encode()).hexdigest()


def state(db, tenant, slot, pairs=None):
    pairs = targets(db, tenant.id, slot.service_id, slot.staff_id) if pairs is None else pairs
    if not pairs:
        return 'unavailable' if slot.calendar_signature else 'available'
    service = row(db, Service, tenant.id, slot.service_id)
    if (not in_hours(tenant, slot.starts_at, slot.ends_at) or slot.starts_at > now() + timedelta(days=60)
            or slot.ends_at - slot.starts_at != timedelta(minutes=service.duration_minutes)
            or slot.calendar_signature and slot.calendar_signature != signature(tenant, service, pairs)):
        return 'unavailable'
    for binding, connection in pairs:
        cache = connection.busy
        if (connection.status != 'connected' or connection.last_error or not cache
                or datetime.fromisoformat(cache['checked']) < now() - timedelta(seconds=90)
                or slot.starts_at < datetime.fromisoformat(cache['start'])
                or slot.ends_at > datetime.fromisoformat(cache['end'])
                or binding.calendar_id not in cache.get('calendars', {})):
            return 'calendar_unavailable'
        if any(datetime.fromisoformat(b['start']) < slot.ends_at and datetime.fromisoformat(b['end']) > slot.starts_at
               for b in cache['calendars'][binding.calendar_id]):
            return 'calendar_busy'
    return 'available'


def overlap(db, tenant_id, slot, pairs, *, excluding=None):
    calendars = {b.calendar_id for b, c in pairs}
    rows = db.execute(select(Booking, Slot).join(Slot, Slot.id == Booking.slot_id).where(
        Booking.tenant_id == tenant_id, Slot.tenant_id == tenant_id, active_booking(),
        Slot.starts_at < slot.ends_at, Slot.ends_at > slot.starts_at))
    for booking, other in rows:
        if booking.id == excluding:
            continue
        linked = ({t['calendar_id'] for t in booking.calendar_targets} if booking.calendar_targets else
                  {b.calendar_id for b, c in targets(db, tenant_id, other.service_id, other.staff_id)})
        if other.staff_id == slot.staff_id or calendars.intersection(linked):
            return True
    return False


def materialize(tenant_id, start, end, service_id=None, staff_id=None):
    with Session.begin() as db:
        tenant = db.get(Tenant, tenant_id, with_for_update=True)
        services = db.scalars(select(Service).where(Service.tenant_id == tenant_id, Service.active)).all()
        staff = db.scalars(select(Staff).where(Staff.tenant_id == tenant_id, Staff.active)).all()
        zone, hours = ZoneInfo(tenant.timezone), validate_hours(tenant.business_hours)
        day, finish, count = max(start, now()).astimezone(zone).date(), min(end, now() + timedelta(days=60)), 0
        while datetime.combine(day, time(), zone) < finish and count < 100:
            window = hours.get(str(day.weekday())) if hours else ['00:00', '23:59']
            for service in services:
                if service_id and service.id != service_id or not window:
                    continue
                for person in staff:
                    if staff_id and person.id != staff_id:
                        continue
                    pairs = targets(db, tenant_id, service.id, person.id)
                    mark = signature(tenant, service, pairs)
                    db.execute(update(Slot).where(Slot.tenant_id == tenant_id, Slot.service_id == service.id,
                        Slot.staff_id == person.id, Slot.calendar_signature.is_not(None),
                        *([Slot.calendar_signature != mark] if pairs else [])).values(active=False))
                    if not pairs:
                        continue
                    cursor = datetime.combine(day, time.fromisoformat(window[0]), zone).astimezone(timezone.utc)
                    close = datetime.combine(day, time.fromisoformat(window[1]), zone).astimezone(timezone.utc)
                    while cursor + timedelta(minutes=service.duration_minutes) <= close and count < 100:
                        stop = cursor + timedelta(minutes=service.duration_minutes)
                        if max(start, now()) <= cursor < finish:
                            identity = str(uuid5(NAMESPACE_URL,
                                f'{tenant_id}:{service.id}:{person.id}:{cursor}:{mark}'))
                            candidate = Slot(id=identity, tenant_id=tenant_id,
                                             service_id=service.id, staff_id=person.id,
                                             starts_at=cursor, ends_at=stop, calendar_signature=mark)
                            if state(db, tenant, candidate, pairs) == 'available' and not overlap(
                                    db, tenant_id, candidate, pairs):
                                if not db.scalar(select(Slot.id).where(
                                        Slot.id == identity, Slot.tenant_id == tenant_id)):
                                    db.add(candidate)
                                count += 1
                        cursor = stop
            day += timedelta(days=1)


def fetch(tenant_id, identity, revision):
    with Session() as db:
        calendars = sorted(set(db.scalars(select(Binding.calendar_id).where(
            Binding.tenant_id == tenant_id, Binding.connection_id == identity))))
    if not calendars:
        return {'busy': {}}
    start, end = now() - timedelta(days=1), now() + timedelta(days=61)
    data = Client(tenant_id).freebusy(access(tenant_id, identity, revision), calendars, start, end)
    return {'busy': {'checked': now().isoformat(), 'start': start.isoformat(), 'end': end.isoformat(),
                     'calendars': data}}


def schedule():
    with Session.begin() as db:
        # Global cron selection only; each task and execution carries the selected tenant.
        for c in db.scalars(select(Connection).where(Connection.status == 'connected').limit(100)):
            if c.busy and datetime.fromisoformat(c.busy['checked']) > now() - timedelta(seconds=45):
                continue
            if db.scalar(select(Binding.id).where(Binding.tenant_id == c.tenant_id,
                                                  Binding.connection_id == c.id).limit(1)) and not db.scalar(
                select(Task.id).where(Task.tenant_id == c.tenant_id, Task.connection_id == c.id,
                    Task.kind == 'availability', Task.state.in_(['queued', 'working'])).limit(1)):
                enqueue(db, c, 'availability')
