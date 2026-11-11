"""Pinned HTTPS, response limits, auth modes and credential reflection redaction."""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from app.integrations.http_actions.contracts import Definition
from app.integrations.http_actions.transport import ActionError, Client, redact
from app.services import web_fetch
from tests.test_http_actions import BODY


class FakeClient(Client):
    def __init__(self):
        self.calls = []

    async def request(self, method, url, headers, **kwargs):
        self.calls.append((method, url, headers, kwargs))
        if url.endswith('/token'):
            return 200, {'access_token': 'private-oauth-token', 'token_type': 'Bearer'}
        return 200, {'stock': 5, 'reflected': headers.get('Authorization') or headers.get('X-API-Key')}


@pytest.mark.parametrize('auth', [{'kind': 'none'}, {'kind': 'bearer', 'value': 'private-token'},
    {'kind': 'api_key', 'value': 'private-api-key'}, {'kind': 'basic', 'username': 'account', 'password': 'password'},
    {'kind': 'oauth_client_credentials', 'token_url': 'https://auth.example.com/token',
     'client_id': 'client', 'client_secret': 'oauth-secret'}])
def test_all_auth_modes_and_redaction(auth):
    definition = Definition.model_validate({**BODY, 'auth': auth})
    private = {'headers': {}, 'auth': definition.auth.model_dump()}
    client = FakeClient()
    code, data = asyncio.run(client.action(definition, private, {'sku': 'SKU/42'}, 'one'))
    assert code == 200 and data['stock'] == 5 and client.calls[-1][1].endswith('SKU%2F42')
    if auth['kind'] != 'none':
        assert data['reflected'] == '[redacted]'
    if auth['kind'] == 'oauth_client_credentials':
        assert len(client.calls) == 2 and client.calls[0][3]['form']['grant_type'] == 'client_credentials'
    assert redact({'x': 'prefix secret'}, ['secret']) == {'x': 'prefix [redacted]'}


def test_idempotency_header_for_writes():
    definition = Definition.model_validate({**BODY, 'method': 'POST', 'allow_changes': True,
                                           'body_template': {'sku': '{sku}'}})
    client = FakeClient()
    asyncio.run(client.action(definition, {'headers': {}, 'auth': definition.auth.model_dump()},
                              {'sku': 'ABC'}, 'call'))
    assert client.calls[0][2]['Idempotency-Key'] == 'http-action-call'
    assert client.calls[0][3]['body'] == {'sku': 'ABC'}


@pytest.mark.parametrize(('status', 'headers', 'chunks', 'expected'), [
    (200, {}, [b'{"ok": true}'], None), (302, {}, [b'{}'], 'redirect_rejected'),
    (200, {'Content-Encoding': 'gzip'}, [b'{}'], 'compressed_response'),
    (200, {}, [b'x' * 100001], 'response_too_large'), (200, {}, [b'not JSON'], 'invalid_json')])
def test_network_contract(monkeypatch, status, headers, chunks, expected):
    requests = []
    class Response:
        status_code = status
        async def aiter_raw(self):
            for chunk in chunks:
                yield chunk
    Response.headers = headers
    class FakeHTTP:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        @asynccontextmanager
        async def stream(self, method, target, **kwargs):
            assert target.host == '93.184.216.34'
            assert kwargs['headers']['Host'] == 'api.example.com' and kwargs['timeout'] == 8
            requests.append(target)
            yield Response()
    monkeypatch.setattr(httpx, 'AsyncClient', FakeHTTP)
    monkeypatch.setattr(web_fetch, 'resolve', lambda _: '93.184.216.34')
    operation = Client().request('GET', 'https://api.example.com/stock', {})
    if expected:
        with pytest.raises(ActionError) as error:
            asyncio.run(operation)
        assert error.value.code == expected
    else:
        assert asyncio.run(operation) == (200, {'ok': True})
    assert len(requests) == 1


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '169.254.169.254', 'fec0::1', '::1'])
def test_dns_rebinding_is_rejected(monkeypatch, ip):
    monkeypatch.setattr(web_fetch, 'resolve', lambda _: ip)
    with pytest.raises(ActionError) as error:
        asyncio.run(Client().request('GET', 'https://api.example.com/data', {}))
    assert error.value.code == 'unsafe_destination'
