"""Fixed Google/Notion origins; streamed byte bounds and customer-safe error codes."""
import time
from contextvars import ContextVar
from urllib.parse import urlsplit

import httpx

from ...observability import integration
from ...services.web_fetch import retry_after

DEADLINE = ContextVar('knowledge_sync_deadline', default=None)
ROOTS = {'drive': 'https://www.googleapis.com/drive/v3/', 'notion': 'https://api.notion.com/v1/',
         'oauth': 'https://oauth2.googleapis.com/'}


class SyncError(RuntimeError):
    def __init__(self, code, *, status=409, wait=30):
        super().__init__(code)
        self.code, self.status, self.wait = code, status, wait


class HTTP:
    @integration('knowledge_sync')
    def request(self, provider, method, path, *, token=None, params=None, payload=None, form=None, binary=False):
        # Paths are constructed from validated IDs, never provider-supplied download URLs.
        if (provider not in ROOTS or method != ('POST' if provider == 'oauth' else 'GET')
                or path.startswith('/') or urlsplit(path).scheme or '..' in path or '?' in path or '#' in path):
            raise SyncError('invalid_operation')
        headers = {'Accept-Encoding': 'identity', **({'Authorization': 'Bearer ' + token} if token else {})}
        if provider == 'notion':
            headers['Notion-Version'] = '2026-03-11'
        seconds = min(30, DEADLINE.get() - time.monotonic()) if DEADLINE.get() else 30
        if seconds <= 0:
            raise SyncError('sync_deadline')
        try:
            with httpx.stream(method, ROOTS[provider] + path, headers=headers, params=params, json=payload,
                              data=form, timeout=seconds, follow_redirects=False) as response:
                code = response.status_code
                if code >= 300:
                    raise SyncError('missing' if code in (404, 410) else 'authorization_required'
                        if code in (401, 403) or provider == 'oauth' and code == 400 else 'provider_unavailable',
                        status=code, wait=min(3600, retry_after(response.headers.get('Retry-After', '30'))))
                data, limit = bytearray(), 10_000_000 if binary else 2_000_000
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > limit or DEADLINE.get() and time.monotonic() > DEADLINE.get():
                        raise SyncError('response_limit')
        except httpx.HTTPError:
            raise SyncError('provider_unavailable') from None
        if binary:
            return bytes(data)
        try:
            import json
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise SyncError('invalid_response') from None
