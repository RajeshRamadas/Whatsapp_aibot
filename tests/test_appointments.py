"""Real PostgreSQL locking, role/isolation checks and booking lifecycle."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import ApiKey, AuditLog, Conversation, Session, now, uid
from app.models.appointments import Booking
from app.services import appointments
from app.services.auth import hash_token

BASE = '/api/v1/appointments'


@pytest.fixture
def setup(client, tenant):
    headers = tenant['headers']
    service = client.post(BASE + '/services', headers=headers,
                          json={'name': 'Consultation', 'duration_minutes': 30}).json()
    staff = client.post(BASE + '/staff', headers=headers, json={'name': 'Demo specialist'}).json()
    start = now() + timedelta(days=2)
    slot_body = {'service_id': service['id'], 'staff_id': staff['id'], 'starts_at': start.isoformat()}
    slot = client.post(BASE + '/slots', headers=headers, json=slot_body)
    assert slot.status_code == 201, slot.text
    with Session.begin() as db:
        conversations = [uid(), uid()]
        for identity in conversations:
            db.add(Conversation(id=identity, tenant_id=tenant['tenant_id'], channel='web', external_user=uid()))
    hold = {'slot_id': slot.json()['id'], 'conversation_id': conversations[0], 'customer_name': 'Demo customer',
            'customer_phone': '+919999000001', 'reminder_opt_in': True, 'language': 'hi'}
    return headers, service, staff, slot_body, slot.json(), hold, conversations


def test_lifecycle_idempotency_and_atomic_rescheduling(client, tenant, setup):
    headers, service, staff, slot_body, slot, hold, conversations = setup
    assert client.get(BASE + '/catalog', headers=headers).json()['services'][0]['name'] == 'Consultation'
    assert client.put(BASE + '/staff/' + staff['id'], headers=headers,
                      json={'name': staff['name'], 'active': True}).status_code == 200
    assert client.put(BASE + '/services/' + service['id'], headers=headers,
                      json={'name': service['name'], 'duration_minutes': 45}).status_code == 200
    first = client.post(BASE + '/bookings', headers=headers, json=hold).json()
    assert first['state'] == 'held' and first['reminder_opt_in'] and first['language'] == 'hi'
    assert 295 < (datetime.fromisoformat(
        first['expires_at']) - now()).total_seconds() <= 300
    assert client.post(BASE + '/bookings', headers=headers, json=hold).json()['id'] == first['id']
    assert client.post(BASE + '/bookings', headers=headers,
                       json={**hold, 'conversation_id': conversations[1]}).status_code == 409
    action = BASE + '/bookings/' + first['id']
    for value in [False, 1, 'true']:
        assert client.post(action + '/confirm', headers=headers, json={'confirm': value}).status_code == 422
    assert client.post(action + '/confirm', headers=headers, json={'confirm': True}).json()['state'] == 'confirmed'
    assert client.delete(BASE + '/slots/' + slot['id'], headers=headers).status_code == 409
    second_slot = client.post(BASE + '/slots', headers=headers,
        json={**slot_body, 'starts_at': (now() + timedelta(days=3)).isoformat()}).json()
    replacement = client.post(BASE + '/bookings', headers=headers,
        json={**hold, 'slot_id': second_slot['id'], 'replaces_id': first['id']}).json()
    assert client.get(BASE + '/bookings', headers=headers).json()[0]['state'] == 'confirmed'
    result = client.post(BASE + '/bookings/' + replacement['id'] + '/confirm', headers=headers, json={'confirm': True})
    assert result.json()['state'] == 'confirmed'
    assert client.get(BASE + '/bookings', headers=headers).json()[0]['state'] == 'cancelled'
    assert client.post(BASE + '/bookings/' + replacement['id'] + '/retry-reminder', headers=headers,
                       json={'confirm': True}).status_code == 409
    assert client.post(BASE + '/bookings/' + replacement['id'] + '/cancel', headers=headers,
                       json={'confirm': True}).json()['reminder_state'] == 'cancelled'
    assert client.delete(BASE + '/slots/' + slot['id'], headers=headers).status_code == 204
    with Session() as db:
        events = db.scalars(select(AuditLog).where(AuditLog.action.like('appointments.%'))).all()
        assert events and all(event.details == {} for event in events)


def test_expiry_conflicts_and_concurrent_reservations(client, tenant, setup):
    headers, service, staff, slot_body, slot, hold, conversations = setup
    with ThreadPoolExecutor(2) as pool:
        def reserve(conversation):
            try:
                return appointments.hold(tenant['tenant_id'], **{**hold, 'conversation_id': conversation})['id']
            except appointments.AppointmentError as exc:
                return exc.status
        results = list(pool.map(reserve, conversations))
    assert results.count(409) == 1
    booking_id = next(item for item in results if item != 409)
    with Session.begin() as db:
        db.get(Booking, booking_id).expires_at = now() - timedelta(seconds=1)
    assert client.post(BASE + '/bookings/' + booking_id + '/confirm', headers=headers,
                       json={'confirm': True}).status_code == 409
    assert client.post(BASE + '/bookings', headers=headers, json=hold).status_code == 201
    assert client.post(BASE + '/slots', headers=headers, json=slot_body).status_code == 409
    assert client.get(BASE + '/slots', headers=headers).json()[0]['state'] == 'held'
    assert client.get(BASE + '/slots', headers=headers, params={'start': '2026-01-01T00:00:00Z',
        'end': '2027-01-01T00:00:00Z'}).status_code == 422
    for path, body in [('/services', {'name': 'Bad', 'duration_minutes': 0}), ('/staff', {'name': ' '}),
                       ('/slots', {**slot_body, 'starts_at': '2027-01-01T09:00:00'}),
                       ('/bookings', {**hold, 'customer_phone': 'invalid'}),
                       ('/bookings', {**hold, 'tenant_id': tenant['tenant_id']})]:
        assert client.post(BASE + path, headers=headers, json=body).status_code == 422


def test_every_route_checks_auth_roles_and_foreign_workspace(client, tenant, setup):
    headers, service, staff, slot_body, slot, hold, conversations = setup
    booking = client.post(BASE + '/bookings', headers=headers, json=hold).json()
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other appointments'}).json()
    foreign = {'X-Admin-Key': other['admin_key']}
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Reader', key_prefix='fixture',
                      key_hash=hash_token('appointment-reader'), scopes=['viewer']))
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Agent', key_prefix='fixture',
                      key_hash=hash_token('appointment-agent'), scopes=['agent']))
    reader, agent = {'Authorization': 'Bearer appointment-reader'}, {'Authorization': 'Bearer appointment-agent'}
    actions = [('GET', '/catalog', None), ('GET', '/slots', None), ('GET', '/bookings', None),
        ('POST', '/services', {'name': 'Service', 'duration_minutes': 30}), ('POST', '/staff', {'name': 'Staff'}),
        ('PUT', '/services/' + service['id'], {'name': service['name'], 'duration_minutes': 30}),
        ('PUT', '/staff/' + staff['id'], {'name': staff['name']}), ('POST', '/slots', slot_body),
        ('DELETE', '/slots/' + slot['id'], None), ('POST', '/bookings', hold)]
    actions += [('POST', '/bookings/' + booking['id'] + '/' + action, {'confirm': True})
                for action in ['confirm', 'cancel', 'retry-reminder']]
    for method, path, body in actions:
        params = {'json': body} if body is not None else {}
        assert client.request(method, BASE + path, **params).status_code == 401
        assert client.request(method, BASE + path, headers=reader, **params).status_code == 403
        wrong_context = {**headers, 'X-Workspace-Id': other['tenant_id']}
        assert client.request(method, BASE + path, headers=wrong_context, **params).status_code == 404
        if method != 'GET' and path not in ('/services', '/staff'):
            assert client.request(method, BASE + path, headers=foreign, **params).status_code == 404
    for path in ['/catalog', '/slots', '/bookings']:
        assert client.get(BASE + path, headers=agent).status_code == 200
    assert client.post(BASE + '/services', headers=agent,
                       json={'name': 'No', 'duration_minutes': 30}).status_code == 403
    assert client.get(BASE + '/slots', headers=foreign, params={'service_id': service['id']}).status_code == 404
    assert client.get(BASE + '/bookings', headers=foreign,
                      params={'conversation_id': conversations[0]}).status_code == 404
    assert client.get(BASE + '/catalog', headers=foreign).json()['services'] == []
    assert client.get(BASE + '/slots', headers=foreign).json() == []
    assert client.post(BASE + '/bookings', headers=headers,
                       json={**hold, 'conversation_id': uid()}).status_code == 404
