"""Public HTTPS JSON requests, bounded across DNS, OAuth, lookup and action IO."""
import asyncio
import base64
import ipaddress
import json
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx

from ...services import web_fetch
from .contracts import render, valid_url

MAX_RESPONSE = 100_000


class ActionError(RuntimeError):
    def __init__(self, code, status=None):
        super().__init__(code)
        self.code, self.status = code, status


def redact(value, secrets):
    def text(value):
        for secret in sorted(set(s for s in secrets if s), key=len, reverse=True):
            value = value.replace(secret, '[redacted]')
        return value
    if isinstance(value, dict):
        return {text(k): redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    return text(value) if isinstance(value, str) else value


class Client:
    async def request(self, method, url, headers, *, body=None, form=None):
        try:
            async with asyncio.timeout(8):
                url = web_fetch.canonical(valid_url(url))
                host = urlsplit(url).hostname
                address = await asyncio.to_thread(web_fetch.resolve, host)
                ip = ipaddress.ip_address(address)
                if not web_fetch.public(address) or getattr(ip, 'is_site_local', False):
                    raise ActionError('unsafe_destination')
                proxy = None if proxy_bypass(host) else getproxies().get('https', getproxies().get('all'))
                proxy = httpx.Proxy(proxy, ssl_context=httpx.create_ssl_context()
                    if urlsplit(proxy).scheme == 'https' else None) if proxy else None
                transport = httpx.AsyncHTTPTransport(verify=web_fetch.PinnedTLS(host), proxy=proxy,
                                                     trust_env=True, retries=0)
                async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False) as client:
                    target = httpx.URL(url).copy_with(host=address)
                    async with client.stream(method, target, headers={**headers, 'Host': urlsplit(url).netloc,
                            'Accept-Encoding': 'identity', 'User-Agent': 'MarketingAddaActions/1.0'},
                            json=body, data=form, timeout=8) as response:
                        if 300 <= response.status_code < 400:
                            raise ActionError('redirect_rejected', response.status_code)
                        if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                            raise ActionError('compressed_response', response.status_code)
                        data = bytearray()
                        async for chunk in response.aiter_raw():
                            data.extend(chunk)
                            if len(data) > MAX_RESPONSE:
                                raise ActionError('response_too_large', response.status_code)
                        try:
                            def invalid_constant(value):
                                raise ValueError()
                            value = json.loads(data.decode('utf-8'), parse_constant=invalid_constant)
                        except (ValueError, UnicodeError):
                            raise ActionError('invalid_json', response.status_code) from None
                        return response.status_code, value
        except ActionError:
            raise
        except (web_fetch.FetchError, ValueError):
            raise ActionError('unsafe_destination') from None
        except TimeoutError:
            raise ActionError('timeout') from None
        except (httpx.HTTPError, OSError):
            raise ActionError('connection_failed') from None

    async def authorize(self, private):
        auth, headers = private['auth'], dict(private['headers'])
        secrets = [*headers.values(), *[auth.get(k, '') for k in (
            'value', 'password', 'username', 'client_secret', 'client_id')]]
        if auth['kind'] == 'api_key':
            headers[auth['header']] = auth['value']
        elif auth['kind'] in ('basic', 'bearer'):
            value = ('Bearer ' + auth['value'] if auth['kind'] == 'bearer' else 'Basic ' +
                     base64.b64encode((auth['username'] + ':' + auth['password']).encode()).decode())
            headers['Authorization'] = value
            secrets.extend([value, value.split(' ', 1)[1]])
        elif auth['kind'] == 'oauth_client_credentials':
            basic = base64.b64encode((auth['client_id'] + ':' + auth['client_secret']).encode()).decode()
            code, token = await self.request('POST', auth['token_url'], {'Authorization': 'Basic ' + basic},
                form={'grant_type': 'client_credentials', **({'scope': auth['scope']} if auth['scope'] else {})})
            if (not 200 <= code < 300 or not isinstance(token, dict) or token.get('token_type', '').lower() != 'bearer'
                    or not isinstance(token.get('access_token'), str) or not 1 <= len(token['access_token']) <= 4096
                    or '\n' in token['access_token'] or '\r' in token['access_token']):
                raise ActionError('auth_failed', code)
            headers['Authorization'] = 'Bearer ' + token['access_token']
            secrets.extend([token['access_token'], headers['Authorization'], basic])
        return headers, secrets

    async def action(self, definition, private, values, request_id, *, lookup=False):
        headers, secrets = await self.authorize(private)
        if definition.method != 'GET' and not lookup:
            headers['Idempotency-Key'] = 'http-action-' + request_id
        url = render(definition.verification_url if lookup else definition.url_template, values, url=True)
        body = None if lookup else render(definition.body_template, values)
        code, raw = await self.request('GET' if lookup else definition.method, url, headers, body=body)
        return code, redact(raw, secrets)
