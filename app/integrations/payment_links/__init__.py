"""Razorpay Payment Links v1, always authenticated with the workspace's own keys."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from ...config import settings
from ...observability import integration
from ...rate_limits import RateLimiter


class PaymentError(RuntimeError):
    def __init__(self, code, *, retry=False, wait=0, uncertain=False):
        super().__init__(code)
        self.code, self.retry, self.wait, self.uncertain = code, retry, wait, uncertain


def safe_url(value):
    if not isinstance(value, str) or len(value) > 1024:
        raise PaymentError('invalid_response')
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise PaymentError('invalid_response') from None
    if url.scheme != 'https' or url.hostname != 'rzp.io' or port not in (None, 443) or url.username or url.password:
        raise PaymentError('invalid_response')
    return value


class Razorpay:
    def __init__(self, tenant_id, credentials):
        self.tenant_id, self.credentials = tenant_id, credentials

    @integration('payment_links')
    def request(self, method, path='', *, payload=None, params=None):
        if not RateLimiter(settings.payment_link_requests_per_minute, namespace='payment-links').allow(self.tenant_id):
            raise PaymentError('rate_limited', retry=True, wait=30)
        try:
            response = httpx.request(method, 'https://api.razorpay.com/v1/payment_links' + path,
                auth=(self.credentials['key_id'], self.credentials['key_secret']), json=payload, params=params,
                timeout=httpx.Timeout(30, connect=10), follow_redirects=False)
        except httpx.HTTPError:
            raise PaymentError('provider_unavailable', retry=True, uncertain=method == 'POST') from None
        status = response.status_code
        if status >= 300:
            raw = response.headers.get('Retry-After', '')
            try:
                wait = int(raw) if raw.isdigit() else max(0, int((parsedate_to_datetime(raw)
                    - datetime.now(timezone.utc)).total_seconds())) if raw else 0
            except (ValueError, TypeError, OverflowError):
                wait = 0
            raise PaymentError('credentials_rejected' if status in (401, 403) else 'provider_rejected',
                retry=status in (408, 429) or status >= 500, wait=wait,
                uncertain=method == 'POST' and (status in (408, 429) or status >= 500))
        try:
            data = response.json()
            if not isinstance(data, dict) or len(response.content) > 1048576:
                raise ValueError()
            return data
        except ValueError:
            raise PaymentError('invalid_response', uncertain=method == 'POST') from None

    def connect(self):
        return self.test()

    def test(self):
        data = self.request('GET', params={'count': 1})
        if not isinstance(data.get('items'), list):
            raise PaymentError('invalid_response')

    def disconnect(self):
        # API keys are removed locally; revocation is performed in the merchant Razorpay dashboard.
        return None

    def find(self, reference):
        items = self.request('GET', params={'reference_id': reference, 'count': 2}).get('items')
        if not isinstance(items, list) or len(items) > 1 or any(i.get('reference_id') != reference for i in items):
            raise PaymentError('invalid_response')
        return items[0] if items else None

    def fetch(self, identity):
        return self.request('GET', '/' + identity)

    def create(self, payload):
        return self.request('POST', payload=payload)

    def cancel(self, identity):
        return self.request('POST', '/' + identity + '/cancel')
