"""Calendar v3 transport failures never expose provider bodies or follow redirects."""
import httpx
import pytest

from app.db import uid
from app.integrations.google_calendar import ROOT, TOKEN, CalendarError, Client


@pytest.mark.parametrize(('status', 'code', 'retry'), [(302, 'provider_rejected', False),
    (401, 'authorization_required', False), (409, 'event_conflict', False),
    (429, 'provider_rejected', True), (503, 'provider_rejected', True)])
def test_transport_safe_errors_timeout_and_retry_after(monkeypatch, status, code, retry):
    def request(method, url, **kwargs):
        assert url == ROOT + 'freeBusy' and kwargs['follow_redirects'] is False
        assert kwargs['timeout'].connect <= 10 and kwargs['timeout'].read <= 30
        return httpx.Response(status, headers={'Retry-After': '90'}, text='private provider response')
    monkeypatch.setattr(httpx, 'request', request)
    with pytest.raises(CalendarError) as failure:
        Client(uid()).request('POST', 'freeBusy', token='private token')
    assert str(failure.value) == code and failure.value.retry is retry and failure.value.wait == 90


def test_transport_invalid_json_missing_event_and_expired_grant(monkeypatch):
    monkeypatch.setattr(httpx, 'request', lambda *a, **k: httpx.Response(200, text='private non-JSON response'))
    with pytest.raises(CalendarError, match='invalid_response'):
        Client(uid()).request('GET', 'users/me/calendarList')
    monkeypatch.setattr(httpx, 'request', lambda *a, **k: httpx.Response(404))
    assert Client(uid()).request('GET', 'calendars/calendar/events/event', missing=True) is None
    monkeypatch.setattr(httpx, 'request', lambda *a, **k: httpx.Response(400, text='invalid grant details'))
    with pytest.raises(CalendarError, match='authorization_required'):
        Client(uid()).request('POST', TOKEN, data={'code': 'private code'})
