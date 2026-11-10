"""Public HTTPS fetches pinned to validated IPs, retaining TLS identity and configured proxies."""
import asyncio
import hashlib
import ipaddress
import re
import socket
import ssl
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import getproxies, proxy_bypass
from uuid import uuid4

import httpx
from redis.asyncio import Redis

from ..config import settings

AGENT = 'MarketingAddaKnowledgeBot/1.0 (+https://marketingadda.com)'


class FetchError(ValueError):
    def __init__(self, message, retry=0):
        super().__init__(message)
        self.retry = retry


def public(address):
    ip = ipaddress.ip_address(address)
    return (ip.is_global and not ip.is_multicast and not ip.is_unspecified and not getattr(ip, 'scope_id', None) and
            not getattr(ip, 'ipv4_mapped', None) and not getattr(ip, 'sixtofour', None) and
            not getattr(ip, 'teredo', None))


def canonical(value, host=None):
    try:
        parts = urlsplit(value)
        name = (parts.hostname or '').encode('idna').decode().lower().rstrip('.')
        if (parts.scheme != 'https' or not name or '%' in name or parts.username is not None or
                parts.password is not None or parts.port not in (None, 443) or len(value) > 2048 or
                any(ord(char) <= 32 or ord(char) == 127 for char in value) or
                name == 'localhost' or name.endswith(('.localhost', '.local', '.internal'))):
            raise ValueError
        try:
            address = ipaddress.ip_address(name)
        except ValueError:
            address = None
        if address and not public(name):
            raise ValueError
        if not address and (not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', name)
                            or name.rsplit('.', 1)[-1].isdigit()):
            raise ValueError
        if host and host != name:
            raise FetchError('Crawling stays on the starting host. Use the final public website address.')
        authority = f'[{name}]' if ':' in name else name
        result = str(httpx.URL(urlunsplit(('https', authority, parts.path or '/', parts.query, ''))))
        if len(result) > 2048:
            raise ValueError
        return result
    except FetchError:
        raise
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise FetchError('Use a public HTTPS address on port 443 without embedded credentials.') from None


def resolve(host):
    addresses = {row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    if not addresses or not all(public(address) for address in addresses):
        raise FetchError('The website address must resolve only to public addresses.')
    return sorted(addresses)[0]


class PinnedTLS(ssl.SSLContext):
    def __new__(cls, hostname):
        return super().__new__(cls, ssl.PROTOCOL_TLS_CLIENT)

    def __init__(self, hostname):
        self.hostname = hostname
        self.load_default_certs()

    def wrap_socket(self, *args, **kwargs):
        kwargs['server_hostname'] = self.hostname
        return super().wrap_socket(*args, **kwargs)

    def wrap_bio(self, *args, **kwargs):
        kwargs['server_hostname'] = self.hostname
        return super().wrap_bio(*args, **kwargs)


def retry_after(value):
    try:
        if value.isdecimal() and len(value) <= 9:
            return max(1, int(value))
        return max(1, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return 30


class Fetcher:
    def __init__(self):
        self.redis = Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        self.interval = 1

    async def close(self):
        await self.redis.aclose()

    async def pause(self, host, seconds):
        key = 'crawl-host:' + hashlib.sha256(host.encode()).hexdigest()
        await self.redis.eval("local ttl=redis.call('PTTL',KEYS[1]); if ttl<0 then "
            "redis.call('SET',KEYS[1],'pause','PX',ARGV[1]); elseif ttl<tonumber(ARGV[1]) then "
            "redis.call('PEXPIRE',KEYS[1],ARGV[1]) end", 1, key, int(max(1, seconds) * 1000))

    @asynccontextmanager
    async def permit(self, host):
        key, token = 'crawl-host:' + hashlib.sha256(host.encode()).hexdigest(), str(uuid4())
        async with asyncio.timeout(65):
            while not await self.redis.set(key, token, nx=True, px=60000):
                delay = max(0.1, await self.redis.pttl(key) / 1000)
                if delay > 60:
                    raise FetchError('The website requested a pause before crawling again.', delay)
                await asyncio.sleep(min(delay, 1))
        try:
            yield
        finally:
            await self.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then "
                                  "return redis.call('PEXPIRE',KEYS[1],ARGV[2]) end", 1, key, token,
                                  int(max(1, self.interval) * 1000))

    async def get(self, value, *, allowed=None):
        host = urlsplit(value).hostname
        for _ in range(5):
            value = canonical(value, host)
            if allowed and not allowed(value):
                raise FetchError('A redirect reached a page disallowed by robots.txt.')
            try:
                address = await asyncio.wait_for(asyncio.to_thread(resolve, host), 5)
                proxy = None if proxy_bypass(host) else getproxies().get('https', getproxies().get('all'))
                proxy = httpx.Proxy(proxy, ssl_context=httpx.create_ssl_context()
                    if urlsplit(proxy).scheme == 'https' else None) if proxy else None
                transport = httpx.AsyncHTTPTransport(verify=PinnedTLS(host), proxy=proxy, trust_env=True, retries=0)
                async with self.permit(host), asyncio.timeout(20):
                    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
                        target = httpx.URL(value).copy_with(host=address)
                        async with client.stream('GET', target, headers={'Host': urlsplit(value).netloc,
                            'User-Agent': AGENT, 'Accept-Encoding': 'identity'}, timeout=10) as response:
                            if response.status_code in (429, 503):
                                self.interval = max(self.interval,
                                    retry_after(response.headers.get('retry-after', '30')))
                                raise FetchError('The website is busy; crawling will retry later.', self.interval)
                            if response.status_code >= 500 or response.status_code == 408:
                                raise FetchError('The website is temporarily unavailable.', 30)
                            if response.status_code in (301, 302, 303, 307, 308):
                                value = canonical(urljoin(value, response.headers.get('location', '')), host)
                                continue
                            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                                raise FetchError('The website did not provide an uncompressed text response.')
                            data = bytearray()
                            async for chunk in response.aiter_raw():
                                data.extend(chunk)
                                if len(data) > 1048576:
                                    raise FetchError('The website page exceeds the text download limit.')
                            return value, response.status_code, bytes(data).decode(response.encoding or 'utf-8',
                                errors='replace'), response.headers
            except FetchError:
                raise
            except Exception:
                raise FetchError('The website could not be reached safely. Crawling will retry later.', 30) from None
        raise FetchError('The website redirected too many times.')
