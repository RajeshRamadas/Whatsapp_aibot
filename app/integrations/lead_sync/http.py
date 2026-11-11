"""Fixed-origin provider transport with safe errors, quotas and bounded responses."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from ...config import settings
from ...observability import integration
from ...rate_limits import RateLimiter


class SyncError(RuntimeError):
    def __init__(self, code, *, retry=False, wait=0, uncertain=False):
        super().__init__(code)
        self.code, self.retry, self.wait, self.uncertain = code, retry, wait, uncertain


class HTTP:
    def __init__(self, tenant_id, provider, root, headers=None):
        self.tenant_id, self.provider, self.root, self.headers = tenant_id, provider, root, headers or {}

    @integration('lead_sync')
    def request(self, method, path='', *, payload=None, params=None, data=None):
        if not RateLimiter(settings.lead_sync_requests_per_minute,
                           namespace='lead-sync-' + self.provider).allow(self.tenant_id):
            raise SyncError('rate_limited', retry=True, wait=30)
        try:
            response = httpx.request(method, self.root + path, headers=self.headers, params=params,
                json=payload, data=data, timeout=httpx.Timeout(30, connect=10), follow_redirects=False)
        except httpx.HTTPError:
            raise SyncError('provider_unavailable', retry=True, uncertain=method == 'POST') from None
        status = response.status_code
        if status >= 300:
            raw = response.headers.get('Retry-After', '')
            try:
                wait = int(raw) if raw.isdigit() else max(0, int((parsedate_to_datetime(raw)
                    - datetime.now(timezone.utc)).total_seconds())) if raw else 0
            except (TypeError, ValueError, OverflowError):
                wait = 0
            raise SyncError('credentials_required' if status in (401, 403) else (
                'record_conflict' if status == 409 else 'provider_rejected'),
                retry=status in (408, 409, 429) or status >= 500, wait=wait,
                uncertain=method == 'POST' and (status in (408, 429) or status >= 500))
        try:
            value = response.json() if response.content else {}
            if len(response.content) > 4 * 1024 * 1024 or not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise SyncError('invalid_response', uncertain=method == 'POST') from None
