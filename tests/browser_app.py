"""ASGI app for browser CI with an isolated Razorpay fake."""
from datetime import timedelta

from app.api import auth, billing
from app.config import settings
from app.db import now
from app.invoice_config import InvoiceIssuer
from app.main import app
from app.rate_limits import RateLimiter
from tests.test_billing import FakeBilling

__all__ = ['app']


class BrowserBilling(FakeBilling):
    def fetch(self, subscription):
        snapshot = super().fetch(subscription)
        snapshot.update(status='active', current_end=int((now() + timedelta(days=30)).timestamp()))
        self.remote = snapshot
        return snapshot


auth._auth_limiter = RateLimiter(30, window_seconds=900, namespace='browser-test-auth')
graph = BrowserBilling()
settings.razorpay_key_id = 'browser-test-key'
settings.razorpay_key_secret = 'browser-test-secret'
settings.razorpay_webhook_secret = 'browser-test-webhook'
settings.razorpay_plan_ids = {'starter': 'plan_starter', 'growth': 'plan_growth'}
settings.gst_invoice_issuer = InvoiceIssuer(
    name='Browser Test Supplier', address='Test office', state='27', postal_code='400001',
    gstin='27ABCDE1234F1Z5', sac='998314', tax_rate=18,
)
billing.provider = lambda: graph
