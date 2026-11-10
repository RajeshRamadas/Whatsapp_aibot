"""Atomic, tenant-scoped appointment availability; no provider or HTTP calls."""
import re
from datetime import timedelta

from sqlalchemy import and_, func, or_, select, update

from ..db import Channel, Conversation, Session, Template, Tenant, now
from ..models.appointments import Booking, Service, Slot, Staff
from .audit import audit
from .customer_languages import normalize
from .templates import variable_count


class AppointmentError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def row(db, model, tenant_id, identity, *, lock=False):
    query = select(model).where(model.id == identity, model.tenant_id == tenant_id)
    value = db.scalar(query.with_for_update() if lock else query)
    if value is None:
        raise AppointmentError('Appointment record not found', 404)
    return value


def fields(value, names):
    return {name: getattr(value, name) for name in names.split()}


def active_booking():
    return or_(Booking.state == 'confirmed', and_(Booking.state == 'held', Booking.expires_at > now()))


def catalog(tenant_id):
    with Session() as db:
        return {'services': [fields(s, 'id name duration_minutes active reminder_minutes reminder_template_id')
                             for s in db.scalars(select(Service).where(Service.tenant_id == tenant_id)
                                                  .order_by(Service.name, Service.id).limit(100))],
                'staff': [fields(s, 'id name active') for s in db.scalars(select(Staff)
                          .where(Staff.tenant_id == tenant_id).order_by(Staff.name, Staff.id).limit(100))]}


def save_resource(tenant_id, kind, values, identity=None, actor=('system', None)):
    model = Service if kind == 'services' else Staff
    with Session.begin() as db:
        if model is Service and values.get('reminder_template_id'):
            template = db.scalar(select(Template).join(Channel).where(Channel.tenant_id == tenant_id,
                Template.id == values['reminder_template_id'], Template.category == 'UTILITY',
                Template.status == 'APPROVED', Template.body.is_not(None)))
            if template is None:
                raise AppointmentError('Approved utility reminder template not found', 404)
            if variable_count(template.body) != 3:
                raise AppointmentError('Reminder template needs three variables: service, staff, time', 422)
        if identity:
            value = row(db, model, tenant_id, identity, lock=True)
        else:
            if db.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id)) >= 100:
                raise AppointmentError('At most 100 appointment resources of each type are supported')
            value = model(tenant_id=tenant_id)
            db.add(value)
        for key, item in values.items():
            setattr(value, key, item)
        db.flush()
        audit(db, tenant_id, 'appointments.' + kind + '.saved', value.id,
              actor_type=actor[0], actor_user_id=actor[1])
        return fields(value, 'id ' + ' '.join(values))


def add_slot(tenant_id, service_id, staff_id, starts_at, actor=('system', None)):
    with Session.begin() as db:
        staff = row(db, Staff, tenant_id, staff_id, lock=True)  # Serialize overlapping availability for this staff.
        service = row(db, Service, tenant_id, service_id)
        if not staff.active or not service.active or not now() < starts_at <= now() + timedelta(days=366):
            raise AppointmentError('Choose active resources and a future time within one year', 422)
        ends_at = starts_at + timedelta(minutes=service.duration_minutes)
        overlap = db.scalar(select(Slot.id).where(Slot.tenant_id == tenant_id, Slot.staff_id == staff.id,
            Slot.active, Slot.starts_at < ends_at, Slot.ends_at > starts_at).limit(1))
        if overlap:
            raise AppointmentError('This staff member already has overlapping availability')
        value = Slot(tenant_id=tenant_id, service_id=service.id, staff_id=staff.id,
                     starts_at=starts_at, ends_at=ends_at)
        db.add(value)
        db.flush()
        audit(db, tenant_id, 'appointments.slot.created', value.id, actor_type=actor[0], actor_user_id=actor[1])
        return fields(value, 'id service_id staff_id starts_at ends_at')


def remove_slot(tenant_id, identity, actor=('system', None)):
    with Session.begin() as db:
        value = row(db, Slot, tenant_id, identity, lock=True)
        if db.scalar(select(Booking.id).where(Booking.tenant_id == tenant_id,
                                             Booking.slot_id == identity, active_booking()).limit(1)):
            raise AppointmentError('Cancel the booking or wait for its hold to expire first')
        value.active = False
        audit(db, tenant_id, 'appointments.slot.removed', identity, actor_type=actor[0], actor_user_id=actor[1])


def slots(tenant_id, start, end, service_id=None, staff_id=None):
    with Session() as db:
        if service_id:
            row(db, Service, tenant_id, service_id)
        if staff_id:
            row(db, Staff, tenant_id, staff_id)
        query = select(Slot, Service.name, Staff.name, Booking.state, Booking.expires_at).join(
            Service, and_(Service.id == Slot.service_id, Service.tenant_id == tenant_id)).join(
            Staff, and_(Staff.id == Slot.staff_id, Staff.tenant_id == tenant_id)).outerjoin(
            Booking, and_(Booking.slot_id == Slot.id, Booking.tenant_id == tenant_id, active_booking())).where(
                Slot.tenant_id == tenant_id, Slot.active, Service.active, Staff.active,
                Slot.starts_at >= start, Slot.starts_at < end)
        if service_id:
            query = query.where(Slot.service_id == service_id)
        if staff_id:
            query = query.where(Slot.staff_id == staff_id)
        return [{**fields(s, 'id service_id staff_id starts_at ends_at'), 'service': service, 'staff': staff,
                 'state': state or 'available', 'expires_at': expiry if state == 'held' else None}
                for s, service, staff, state, expiry in db.execute(query.order_by(Slot.starts_at, Slot.id).limit(100))]


def booking_view(db, booking):
    slot = row(db, Slot, booking.tenant_id, booking.slot_id)
    service = row(db, Service, booking.tenant_id, slot.service_id)
    staff = row(db, Staff, booking.tenant_id, slot.staff_id)
    state = 'expired' if booking.state == 'held' and booking.expires_at <= now() else booking.state
    return {**fields(booking, 'id slot_id conversation_id customer_name customer_phone language expires_at replaces_id '
                    'reminder_opt_in reminder_state reminder_error reminder_message_id'),
            **fields(slot, 'starts_at ends_at'), 'service': service.name, 'staff': staff.name, 'state': state}


def bookings(tenant_id, start, end, conversation_id=None):
    with Session() as db:
        if conversation_id:
            row(db, Conversation, tenant_id, conversation_id)
        query = select(Booking).join(Slot).where(Booking.tenant_id == tenant_id, Slot.tenant_id == tenant_id,
                                               Slot.starts_at >= start, Slot.starts_at < end)
        if conversation_id:
            query = query.where(Booking.conversation_id == conversation_id)
        return [booking_view(db, b) for b in db.scalars(query.order_by(Slot.starts_at, Booking.id).limit(100))]


def hold(tenant_id, slot_id, conversation_id, customer_name, customer_phone=None, reminder_opt_in=False,
         replaces_id=None, language=None, actor=('system', None)):
    with Session.begin() as db:
        slot = row(db, Slot, tenant_id, slot_id, lock=True)
        service, staff = row(db, Service, tenant_id, slot.service_id), row(db, Staff, tenant_id, slot.staff_id)
        conversation = row(db, Conversation, tenant_id, conversation_id)
        language = normalize(language or db.get(Tenant, tenant_id).default_language)
        phone = conversation.external_user if conversation.channel == 'whatsapp' else customer_phone
        if not re.fullmatch(r'\+?[1-9][0-9]{6,14}', phone or '') or not customer_name.strip():
            raise AppointmentError('Provide the customer name and an international phone number', 422)
        if not slot.active or not service.active or not staff.active or slot.starts_at <= now():
            raise AppointmentError('This slot is no longer available')
        if replaces_id:
            previous = row(db, Booking, tenant_id, replaces_id)
            if previous.conversation_id != conversation_id:
                raise AppointmentError('Appointment record not found', 404)
            if (previous.state != 'confirmed' or previous.slot_id == slot_id
                    or row(db, Slot, tenant_id, previous.slot_id).starts_at <= now()):
                raise AppointmentError('Only a confirmed booking can be rescheduled to another slot')
        db.execute(update(Booking).where(Booking.tenant_id == tenant_id, Booking.slot_id == slot_id,
                   Booking.state == 'held', Booking.expires_at <= now()).values(state='expired'))
        existing = db.scalar(select(Booking).where(Booking.tenant_id == tenant_id,
                                                   Booking.slot_id == slot_id, active_booking()))
        if existing:
            if (existing.conversation_id, existing.customer_name, existing.customer_phone, existing.replaces_id,
                existing.reminder_opt_in, existing.language) == (
                    conversation_id, customer_name, phone, replaces_id, reminder_opt_in, language):
                return booking_view(db, existing)
            raise AppointmentError('Another customer has reserved this slot')
        value = Booking(tenant_id=tenant_id, slot_id=slot_id, conversation_id=conversation_id,
            customer_name=customer_name, customer_phone=phone, language=language,
            expires_at=now() + timedelta(minutes=5),
            reminder_opt_in=reminder_opt_in, reminder_at=slot.starts_at - timedelta(minutes=service.reminder_minutes),
            replaces_id=replaces_id)
        db.add(value)
        db.flush()
        audit(db, tenant_id, 'appointments.held', value.id, actor_type=actor[0], actor_user_id=actor[1])
        return booking_view(db, value)


def transition(tenant_id, identity, action, conversation_id=None, actor=('system', None)):
    if action not in ('confirm', 'cancel', 'retry-reminder'):
        raise AppointmentError('Unknown appointment action', 422)
    with Session.begin() as db:
        snapshot = row(db, Booking, tenant_id, identity)
        if conversation_id and snapshot.conversation_id != conversation_id:
            raise AppointmentError('Appointment record not found', 404)
        previous = row(db, Booking, tenant_id, snapshot.replaces_id) if snapshot.replaces_id else None
        # Consistent ordering also protects simultaneous reschedules and cancellations.
        identities = sorted({snapshot.slot_id, *([previous.slot_id] if previous else [])})
        for slot_id in identities:
            row(db, Slot, tenant_id, slot_id, lock=True)
        value = row(db, Booking, tenant_id, identity, lock=True)
        slot = row(db, Slot, tenant_id, value.slot_id)
        if action == 'confirm':
            if value.state == 'confirmed':
                return booking_view(db, value)
            if value.state != 'held' or value.expires_at <= now() or slot.starts_at <= now():
                raise AppointmentError('The hold expired or is no longer available')
            if not slot.active or not row(db, Service, tenant_id, slot.service_id).active or not row(
                    db, Staff, tenant_id, slot.staff_id).active:
                raise AppointmentError('This slot is no longer available')
            if previous:
                previous = row(db, Booking, tenant_id, previous.id, lock=True)
                if (previous.state != 'confirmed' or previous.conversation_id != value.conversation_id
                        or row(db, Slot, tenant_id, previous.slot_id).starts_at <= now()):
                    raise AppointmentError('The original booking changed; create a new hold')
                previous.state, previous.reminder_state = 'cancelled', 'cancelled'
            value.state = 'confirmed'
        elif action == 'cancel':
            value.state, value.reminder_state = 'cancelled', 'cancelled'
        else:
            if value.state != 'confirmed' or value.reminder_state != 'blocked' or slot.starts_at <= now():
                raise AppointmentError('Only blocked future reminders can be retried')
            value.reminder_state, value.reminder_error = 'pending', None
        audit(db, tenant_id, 'appointments.' + action, value.id, actor_type=actor[0], actor_user_id=actor[1])
        return booking_view(db, value)
