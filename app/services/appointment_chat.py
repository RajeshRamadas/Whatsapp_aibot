"""Customer-bound appointment tools; only literal customer commands confirm/cancel."""
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select

from .. import llm
from ..db import Conversation, Session, now
from ..models.appointments import Booking
from . import appointments
from .customer_languages import LANGUAGES, locale_for, normalize
from .events import publish_sync

COPY = {code: json.loads((Path(__file__).parents[1] / 'messages' / 'appointments' / f'{code}.json').read_text())
        for code in LANGUAGES}
COMMAND = re.compile(r'(BOOK|REMIND|CANCEL) ([a-f0-9]{12})', re.I)


def reference(identity):
    return identity.replace('-', '')[:12]


def local_time(tenant, at):
    try:
        zone = ZoneInfo(tenant.timezone)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo('UTC')
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    return at.astimezone(zone).strftime('%Y-%m-%d %H:%M') + ' ' + str(zone)


def notice(tenant, value, kind):
    text = COPY[normalize(value['language'])][kind].format(**{
        **value, 'id': reference(value['id']), 'time': local_time(tenant, value['starts_at']),
    })
    if kind == 'confirmed':
        text += ' ' + COPY[normalize(value['language'])]['reminderOn' if value['reminder_opt_in'] else 'reminderOff']
    return text


def command(tenant, conversation_id, question):
    match = COMMAND.fullmatch(question.strip()) if conversation_id else None
    if not match:
        return None
    language = normalize(tenant.default_language)
    try:
        with Session() as db:
            rows = db.scalars(select(Booking).where(Booking.tenant_id == tenant.id,
                Booking.conversation_id == conversation_id,
                func.replace(Booking.id, '-', '').startswith(match[2].lower()))).all()
            if len(rows) != 1:  # Never guess if a short reference collides.
                raise appointments.AppointmentError('Appointment not found', 404)
            value = rows[0]
            language = value.language
        action = 'cancel' if match[1].upper() == 'CANCEL' else 'confirm'
        value = appointments.transition(tenant.id, value.id, action, conversation_id=conversation_id,
                                        reminder_opt_in=True if match[1].upper() == 'REMIND' else None)
    except appointments.AppointmentError:
        return COPY[language]['error']
    publish_sync(tenant.id, 'appointments.updated', {})
    return notice(tenant, value, 'cancelled' if action == 'cancel' else 'confirmed')


def tools(tenant, conversation_id, question, language=None):
    result = {}
    if not conversation_id:
        return [], result  # Bot previews can never reserve a real slot.
    catalog = appointments.catalog(tenant.id)
    if not any(s['active'] for s in catalog['services']):
        return [], result
    with Session() as db:
        appointments.row(db, Conversation, tenant.id, conversation_id)
    language = normalize(language) if language else locale_for(question, tenant.default_language)

    def check(args):
        start = now()
        end = start + timedelta(days=30)
        if args.get('date'):
            day = date.fromisoformat(args['date'])
            zone = ZoneInfo(tenant.timezone)
            start = max(now(), datetime.combine(day, time(), zone))
            end = datetime.combine(day + timedelta(days=1), time(), zone)
        rows = appointments.slots(tenant.id, start, end, args.get('service_id'), args.get('staff_id'))
        return {'services': [s for s in catalog['services'] if s['active']],
                'staff': [s for s in catalog['staff'] if s['active']], 'timezone': tenant.timezone,
                'slots': json.loads(json.dumps([r for r in rows if r['state'] == 'available'][:6],
                                               default=lambda value: value.isoformat()))}

    def hold(args):
        if result:
            raise appointments.AppointmentError('Only one hold may be created per turn')
        value = appointments.hold(tenant.id, conversation_id=conversation_id, language=language, **args)
        result.update(value)
        publish_sync(tenant.id, 'appointments.updated', {})
        return {'state': value['state'], 'instructions': notice(tenant, value, value['state'])}

    def mine(args):
        rows = appointments.bookings(tenant.id, now(), now() + timedelta(days=60), conversation_id)
        return [{'id': r['id'], 'reference': reference(r['id']), 'service': r['service'], 'staff': r['staff'],
                 'starts_at': r['starts_at'].isoformat(), 'state': r['state']}
                for r in rows if r['state'] in ('held', 'confirmed')]

    identity = {'type': 'string', 'minLength': 36, 'maxLength': 36}
    return [llm.Tool('check_slots', 'List services/staff and currently free appointment slots.', {
        'type': 'object', 'additionalProperties': False, 'properties': {
            'service_id': identity, 'staff_id': identity, 'date': {'type': 'string', 'format': 'date'}}}, check),
        llm.Tool('hold_appointment', 'Hold one selected slot for five minutes; this does NOT confirm a booking. '
                 'Use only name and phone supplied by the customer. replaces_id reschedules their existing booking.', {
            'type': 'object', 'additionalProperties': False, 'required': ['slot_id', 'customer_name'], 'properties': {
                'slot_id': identity, 'replaces_id': identity,
                'customer_name': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                'customer_phone': {'type': 'string', 'pattern': r'^\+?[1-9][0-9]{6,14}$'}}}, hold),
        llm.Tool('my_bookings', 'List only this customer’s bookings for cancellation or rescheduling.',
                 {'type': 'object', 'additionalProperties': False}, mine)], result
