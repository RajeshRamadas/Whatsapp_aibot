"""Signed public-HTTPS POSTs pinned to validated DNS answers, without redirects."""
import asyncio
import hashlib
import hmac
import ipaddress
import json
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx

from ...services import web_fetch


class DeliveryError(RuntimeError):
    def __init__(self, code, retryable=True):
        super().__init__(code)
        self.code, self.retryable = code, retryable


def body_bytes(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def signature(secret, timestamp, body):
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b'.' + body, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={digest}'


class Transport:
    async def send(self, url, body, headers):
        try:
            async with asyncio.timeout(10):
                value = web_fetch.canonical(url)
                host = urlsplit(value).hostname
                address = await asyncio.to_thread(web_fetch.resolve, host)
                ip = ipaddress.ip_address(address)
                if not web_fetch.public(address) or getattr(ip, 'is_site_local', False):
                    raise DeliveryError('unsafe_destination', False)
                proxy = None if proxy_bypass(host) else getproxies().get('https', getproxies().get('all'))
                proxy = httpx.Proxy(proxy, ssl_context=httpx.create_ssl_context()
                    if urlsplit(proxy).scheme == 'https' else None) if proxy else None
                transport = httpx.AsyncHTTPTransport(verify=web_fetch.PinnedTLS(host), proxy=proxy,
                                                     trust_env=True, retries=0)
                async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False) as client:
                    target = httpx.URL(value).copy_with(host=address)
                    async with client.stream('POST', target, content=body, timeout=10,
                        headers={**headers, 'Host': urlsplit(value).netloc}) as response:
                        # Never read/store an unbounded or sensitive response body.
                        delay = web_fetch.retry_after(response.headers.get('Retry-After', '0'))
                        return response.status_code, min(86400, delay)
        except web_fetch.FetchError:
            raise DeliveryError('unsafe_destination', False) from None
        except (httpx.HTTPError, OSError, TimeoutError):
            raise DeliveryError('receiver_unavailable') from None
