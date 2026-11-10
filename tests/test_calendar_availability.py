"""Calendar bindings, fresh free/busy, working hours and shared-resource holds."""
from datetime import timedelta

from sqlalchemy import select

from app.db import Session, Tenant, now, uid
from app.integrations.google_calendar import Client
from app.models.google_calendar import GoogleConnection as Connection
from app.services import google_jobs
from tests.test_appointments import setup as setup
from tests.test_google_calendar import BASE, authorize
from tests.test_google_calendar import connected as connected


def attach(client, tenant, connected, setup):
    headers, calls, _ = connected
    authorize(client, headers)
    google_jobs.process_one()
    connection = client.get(BASE, headers=headers).json()['connections'][0]
    _, service, staff, _, slot, _, _ = setup
    body = {'connection_id': connection['id'], 'calendar_id': connection['calendars'][0]['id'],
            'service_id': service['id'], 'staff_id': staff['id'], 'confirm_data_sharing': True}
    response = client.post(BASE + '/bindings', headers=headers, json=body)
    assert response.status_code == 201, response.text
    return headers, calls, connection, body, response.json()['id']


def test_bindings_permissions_validation_isolation(client, tenant, connected, setup):
    headers, _, connection, body, identity = attach(client, tenant, connected, setup)
    assert client.get(BASE + '/bindings', headers=headers).json()[0]['id'] == identity
    assert client.post(BASE + '/bindings', headers=headers, json={**body, 'service_id': uid()}).status_code == 404
    assert client.post(BASE + '/bindings', headers=headers,
                       json={**body, 'confirm_data_sharing': False}).status_code == 422
    assert client.post(BASE + '/bindings', headers=headers,
                       json={**body, 'service_id': None, 'staff_id': None}).status_code == 422
    assert client.post(BASE + '/bindings', headers=headers, json={**body, 'connection_id': uid()}).status_code == 404
    assert client.delete(BASE + '/bindings/' + uid(), headers=headers).status_code == 404
    assert client.delete(BASE + '/bindings/invalid', headers=headers).status_code == 422
    assert client.delete(BASE + '/bindings/' + identity, headers=headers).status_code == 204
    client.cookies.clear()
    assert client.get(BASE + '/bindings').status_code == 401
    assert client.post(BASE + '/bindings', json=body).status_code == 401
    assert client.delete(BASE + '/bindings/' + identity).status_code == 401


def test_fresh_busy_hours_shared_holds_and_no_calendar_fallback(client, tenant, connected, setup, monkeypatch):
    headers, _, connection, body, binding_id = attach(client, tenant, connected, setup)
    _, service, staff, _, slot, hold, conversations = setup
    start = now().replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=3)
    end = start + timedelta(hours=2)
    with Session.begin() as db:
        t = db.get(Tenant, tenant['tenant_id'])
        t.timezone, t.business_hours = 'UTC', '{"' + str(start.weekday()) + '":["09:00","11:00"]}'
    monkeypatch.setattr(Client, 'freebusy', lambda *a: {body['calendar_id']: [
        {'start': start.isoformat(), 'end': (start + timedelta(minutes=30)).isoformat()}]})
    assert google_jobs.process_one()
    query = {'start': start.isoformat(), 'end': end.isoformat(), 'service_id': service['id'], 'staff_id': staff['id']}
    response = client.get('/api/v1/appointments/slots', headers=headers, params=query)
    assert response.status_code == 200, response.text
    available = [s for s in response.json() if s['state'] == 'available']
    assert len(available) == 3 and available[0]['starts_at'].startswith(start.strftime('%Y-%m-%d') + 'T09:30')
    selected = {**hold, 'slot_id': available[0]['id']}
    first = client.post('/api/v1/appointments/bookings', headers=headers, json=selected)
    assert first.status_code == 201, first.text
    assert client.post('/api/v1/appointments/bookings', headers=headers,
                       json={**selected, 'conversation_id': conversations[1]}).status_code == 409
    with Session.begin() as db:
        c = db.scalar(select(Connection).where(Connection.id == connection['id'],
            Connection.tenant_id == tenant['tenant_id']))
        c.busy = {**c.busy, 'checked': (now() - timedelta(minutes=2)).isoformat()}
    assert client.post('/api/v1/appointments/bookings', headers=headers,
                       json={**hold, 'slot_id': available[1]['id']}).status_code == 409
    client.delete(BASE + '/bindings/' + binding_id, headers=headers)
    assert client.post('/api/v1/appointments/bookings', headers=headers, json=hold).status_code == 201


def test_foreign_bindings_and_all_admin_routes_deny_viewers(client, tenant, connected, setup):
    from app.db import Membership
    headers, _, connection, body, binding_id = attach(client, tenant, connected, setup)
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Foreign calendar workspace'}).json()
    foreign = {'Authorization': 'Bearer ' + other['admin_key']}
    assert client.get(BASE + '/bindings', headers=foreign).json() == []
    assert client.delete(BASE + '/bindings/' + binding_id, headers=foreign).status_code == 404
    assert client.post(BASE + '/bindings', headers=foreign, json=body).status_code == 404
    with Session.begin() as db:
        m = db.scalar(select(Membership).where(Membership.tenant_id == tenant['tenant_id'],
                                               Membership.user_id == connected[2]))
        m.role = 'viewer'
    for method, path, payload in [('GET', '', None), ('POST', '/connect', {'label': 'Team'}),
            ('POST', '/connections/' + connection['id'] + '/refresh', None),
            ('DELETE', '/connections/' + connection['id'], {'confirm': True}), ('GET', '/bindings', None),
            ('POST', '/bindings', body), ('DELETE', '/bindings/' + binding_id, None)]:
        assert client.request(method, BASE + path, headers=headers, json=payload).status_code == 403


def test_legacy_indian_timezone_hours():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.services import calendar_availability as calendar
    tenant = SimpleNamespace(timezone='Asia/Calcutta', business_hours='{"0":["09:00","10:00"]}')
    assert calendar.in_hours(tenant, datetime(2026, 11, 16, 3, 30, tzinfo=timezone.utc),
                             datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc))
