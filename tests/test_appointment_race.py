"""A cancellation committed while confirmation waits must not be resurrected."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident

from app.db import Session
from app.models.appointments import Booking
from app.services import appointments
from tests.test_appointments import BASE  # noqa: F401
from tests.test_appointments import setup as setup


def test_waiting_confirmation_observes_committed_cancellation(client, tenant, setup, monkeypatch):
    headers, _, _, _, _, hold, _ = setup
    booking = client.post(BASE + '/bookings', headers=headers, json=hold).json()
    ready, resume, worker = Event(), Event(), {}
    original = appointments.row
    def read(db, model, tenant_id, identity, *, lock=False):
        value = original(db, model, tenant_id, identity, lock=lock)
        if model is Booking and identity == booking['id'] and not lock and get_ident() == worker.get('id'):
            ready.set()
            assert resume.wait(5)
        return value
    monkeypatch.setattr(appointments, 'row', read)
    def confirm():
        worker['id'] = get_ident()
        try:
            return appointments.transition(tenant['tenant_id'], booking['id'], 'confirm')['state']
        except appointments.AppointmentError:
            return 'rejected'
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(confirm)
        assert ready.wait(5)
        try:
            appointments.transition(tenant['tenant_id'], booking['id'], 'cancel')
        finally:
            resume.set()
        assert future.result(timeout=5) == 'rejected'
    with Session() as db:
        assert db.get(Booking, booking['id']).state == 'cancelled'
