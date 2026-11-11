"""Merchant transport contract, safe failures, bounded URLs and receipt proof parsing."""
import httpx
import pytest

from app.db import uid
from app.integrations.payment_links import PaymentError, Razorpay, safe_url


@pytest.mark.parametrize(('status', 'retry', 'uncertain'), [(400, False, False), (401, False, False),
                                                         (429, True, True), (503, True, True)])
def test_merchant_transport_uses_only_tenant_keys_and_safe_errors(monkeypatch, status, retry, uncertain):
    def request(method, url, **kwargs):
        assert url == 'https://api.razorpay.com/v1/payment_links'
        assert kwargs['auth'] == ('tenant-key', 'tenant-secret') and kwargs['follow_redirects'] is False
        assert kwargs['timeout'].connect == 10 and kwargs['timeout'].read == 30
        return httpx.Response(status, text='private provider details', headers={'Retry-After': '90'})
    monkeypatch.setattr(httpx, 'request', request)
    with pytest.raises(PaymentError) as error:
        Razorpay(uid(), {'key_id': 'tenant-key', 'key_secret': 'tenant-secret'}).create({'amount': 100})
    assert error.value.retry is retry and error.value.uncertain is uncertain and error.value.wait == 90
    assert 'private' not in str(error.value) and 'tenant-secret' not in str(error.value)


def test_unsafe_payment_urls_and_invalid_provider_json_are_rejected(monkeypatch):
    for url in ('http://rzp.io/a', 'https://evil.example/a', 'https://rzp.io:abc/a', 'https://user@rzp.io/a', None):
        with pytest.raises(PaymentError, match='invalid_response'):
            safe_url(url)
    monkeypatch.setattr(httpx, 'request', lambda *a, **k: httpx.Response(200, text='private invalid JSON'))
    with pytest.raises(PaymentError) as error:
        Razorpay(uid(), {'key_id': 'key', 'key_secret': 'secret'}).create({})
    assert error.value.uncertain and str(error.value) == 'invalid_response'
