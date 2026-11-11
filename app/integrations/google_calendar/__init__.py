"""Google Calendar v3 transport; fixed origins, bounded responses and safe failures."""
import base64
import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode, urlsplit

import httpx

from ...config import settings
from ...observability import integration
from ...rate_limits import RateLimiter

ROOT = 'https://www.googleapis.com/calendar/v3/'
TOKEN = 'https://oauth2.googleapis.com/token'
SCOPES = tuple('https://www.googleapis.com/auth/' + value for value in (
    'calendar.calendarlist.readonly', 'calendar.events', 'calendar.events.freebusy'))


class CalendarError(RuntimeError):
    def __init__(self, code, *, retry=False, wait=0):
        super().__init__(code)
        self.code, self.retry, self.wait = code, retry, wait


def configured():
    url = urlsplit(settings.google_calendar_redirect_uri or '')
    secure = url.scheme == 'https' or (settings.environment != 'production' and url.scheme == 'http'
                                      and url.hostname in ('localhost', '127.0.0.1'))
    return bool(settings.google_calendar_client_id and settings.google_calendar_client_secret and settings.master_key
                and secure and url.hostname and not (url.username or url.password or url.query or url.fragment))


def authorization_url(state, verifier):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': settings.google_calendar_client_id, 'redirect_uri': settings.google_calendar_redirect_uri,
        'response_type': 'code', 'scope': ' '.join(SCOPES), 'state': state, 'code_challenge': challenge,
        'code_challenge_method': 'S256', 'access_type': 'offline', 'prompt': 'consent',
    })


class Client:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id

    @integration('google_calendar')
    def request(self, method, path, *, token=None, data=None, payload=None, params=None, missing=False, headers=None):
        limiter = RateLimiter(settings.google_calendar_requests_per_minute, namespace='google-calendar')
        if not limiter.allow(self.tenant_id):
            raise CalendarError('rate_limited', retry=True, wait=15)
        url = path if path in (TOKEN, 'https://oauth2.googleapis.com/revoke') else ROOT + path
        request_headers = {**(headers or {}), **({'Authorization': 'Bearer ' + token} if token else {})}
        try:
            response = httpx.request(method, url, headers=request_headers, data=data, json=payload,
                                    params=params, timeout=httpx.Timeout(30, connect=10), follow_redirects=False)
        except httpx.HTTPError:
            raise CalendarError('provider_unavailable', retry=True) from None
        if missing and response.status_code in (404, 410):
            return None
        if response.status_code >= 400 or 300 <= response.status_code < 400:
            raw = response.headers.get('Retry-After', '')
            try:
                wait = max(0, int(raw)) if raw.isdigit() else max(0, int(
                    (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())) if raw else 0
            except (TypeError, ValueError, OverflowError):
                wait = 0
            status = response.status_code
            raise CalendarError('authorization_required' if status == 401 else 'provider_rejected',
                                retry=status in (408, 429) or status >= 500, wait=wait)
        if len(response.content) > 4 * 1024 * 1024:
            raise CalendarError('response_too_large')
        try:
            return response.json() if response.content else {}
        except ValueError:
            raise CalendarError('invalid_response') from None

    def connect(self, code, verifier):
        return self.request('POST', TOKEN, data={
            'client_id': settings.google_calendar_client_id, 'client_secret': settings.google_calendar_client_secret,
            'redirect_uri': settings.google_calendar_redirect_uri, 'grant_type': 'authorization_code',
            'code': code, 'code_verifier': verifier,
        })

    def refresh(self, refresh_token):
        return self.request('POST', TOKEN, data={'client_id': settings.google_calendar_client_id,
            'client_secret': settings.google_calendar_client_secret, 'grant_type': 'refresh_token',
            'refresh_token': refresh_token})

    def disconnect(self, refresh_token):
        self.request('POST', 'https://oauth2.googleapis.com/revoke', data={'token': refresh_token})

    def test(self, token):
        rows, page = [], None
        for _ in range(4):
            data = self.request('GET', 'users/me/calendarList', token=token, params={
                'minAccessRole': 'writer', 'maxResults': 250,
                'fields': 'nextPageToken,items(id,summary,timeZone,accessRole)',
                    **({'pageToken': page} if page else {})})
            for item in data.get('items', []):
                if item.get('accessRole') in ('owner', 'writer') and isinstance(item.get('id'), str):
                    rows.append({'id': item['id'], 'name': str(item.get('summary', item['id']))[:200],
                                 'timezone': str(item.get('timeZone', 'UTC'))[:64]})
            if len(rows) > 500:
                raise CalendarError('too_many_calendars')
            page = data.get('nextPageToken')
            if not page:
                return rows
        raise CalendarError('too_many_calendars')

    @staticmethod
    def path(calendar_id, event_id=None):
        path = 'calendars/' + quote(calendar_id, safe='') + '/events'
        return path + '/' + quote(event_id, safe='') if event_id else path

    def freebusy(self, token, calendars, start, end):
        data = self.request('POST', 'freeBusy', token=token, payload={
            'timeMin': start.isoformat(), 'timeMax': end.isoformat(), 'items': [{'id': c} for c in calendars]})
        result = {}
        for identity in calendars:
            item = data.get('calendars', {}).get(identity)
            if not isinstance(item, dict) or item.get('errors') or not isinstance(item.get('busy'), list):
                raise CalendarError('availability_unavailable', retry=True)
            intervals = []
            for busy in item['busy']:
                try:
                    first, last = (datetime.fromisoformat(busy[k].replace('Z', '+00:00')) for k in ('start', 'end'))
                    if not first.tzinfo or not last.tzinfo or first >= last:
                        raise ValueError()
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise CalendarError('invalid_response') from None
                intervals.append({'start': first.isoformat(), 'end': last.isoformat()})
            result[identity] = intervals
        return result
