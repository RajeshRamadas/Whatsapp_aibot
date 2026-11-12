"""ASGI app for browser CI with an isolated Razorpay fake."""
import asyncio
import io
import re
import wave
from datetime import timedelta

import httpx
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import llm, whatsapp
from app.api import auth, billing, knowledge, templates
from app.api import whatsapp as whatsapp_api
from app.config import settings
from app.db import CampaignRecipient, Channel, Session, Template, now, uid
from app.invoice_config import InvoiceIssuer
from app.jobs.worker import dispatch_agent_reply, ingest_source, process_inbound
from app.main import app
from app.rate_limits import RateLimiter
from app.services import campaign_delivery, media, media_intelligence
from app.services import templates as template_service
from tests.test_billing import FakeBilling

__all__ = ['app']


class BrowserKnowledge:
    def embed(self, texts, **kwargs):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def complete(self, *args):
        parts = re.split(r'(?m)^\[([0-9]+)\] ', args[1] if len(args) > 1 else '')
        for number, content in zip(parts[1::2], parts[2::2], strict=True):
            if match := re.search(r'Refunds (?:are )?accepted within [0-9]+ days\.', content):
                return match[0] + ' [' + number + ']'
        return "I don't know based on our documents."

    def verify(self, model, system, messages):
        import json
        payload = json.loads(messages[0]['content'])
        numbers = [s['number'] for s in payload['evidence']]
        return json.dumps({'verdict': 'supported' if numbers else 'non_factual', 'citations': numbers})

    def run(self, *args):
        return llm.ChatResult(self.complete(*args[:3]), [])


async def browser_ingestion(name, **kwargs):
    assert name == 'ingest_source'
    await ingest_source({'job_try': 3}, kwargs['source_id'], kwargs['tenant_id'])
    return kwargs['_job_id']


knowledge.enqueue = browser_ingestion
llm.embedder = lambda: BrowserKnowledge()
llm.chat_provider = lambda name: BrowserKnowledge()
settings.ocr_languages = 'eng'  # The synthetic browser scan is English; no external AI is used.


class BrowserBilling(FakeBilling):
    def fetch(self, subscription):
        snapshot = super().fetch(subscription)
        snapshot.update(status='active', current_end=int((now() + timedelta(days=30)).timestamp()))
        self.remote = snapshot
        return snapshot


auth._auth_limiter = RateLimiter(60, window_seconds=900, namespace='browser-test-auth')
graph = BrowserBilling()
settings.razorpay_key_id = 'browser-test-key'
settings.razorpay_key_secret = 'browser-test-secret'
settings.razorpay_webhook_secret = 'browser-test-webhook'
settings.razorpay_plan_ids = {'starter': 'plan_starter', 'growth': 'plan_growth'}
settings.campaign_sends_per_second = 80
settings.gst_invoice_issuer = InvoiceIssuer(
    name='Browser Test Supplier', address='Test office', state='27', postal_code='400001',
    gstin='27ABCDE1234F1Z5', sac='998314', tax_rate=18,
)
billing.provider = lambda: graph


class BrowserMeta:
    def request(self, path, *, token, params=None, payload=None):
        if params and params.get('fields') == 'owner_business_info':
            return {'owner_business_info': {'id': '987654'}}
        if params and 'quality_rating' in params.get('fields', ''):
            return {'quality_rating': 'GREEN', 'whatsapp_business_manager_messaging_limit': 'TIER_2K'}
        if payload:
            return {'id': 'browser-template', 'status': 'PENDING'}
        with Session() as db:
            rows = db.scalars(select(Template).join(Channel, Channel.id == Template.channel_id)
                .where(Channel.waba_id == path.split('/')[0])).all()
            return {'data': [{'id': row.meta_id, 'name': row.name, 'language': row.language,
                'category': row.category, 'status': 'APPROVED', 'components': [{'type': 'BODY', 'text': row.body}]}
                for row in rows]}


def browser_send(phone, payload, tenant_id, **kwargs):
    identity = payload.get('biz_opaque_callback_data') or uid()
    message_id = 'wamid.browser.' + identity
    if payload.get('biz_opaque_callback_data'):
        campaign_delivery.receipt({'phone_number_id': phone, 'id': message_id, 'status': 'read',
                                  'biz_opaque_callback_data': identity, 'pricing': {'billable': True}})
    return httpx.Response(200, json={'messages': [{'id': message_id}]})


templates.MetaSignup = template_service.MetaSignup = campaign_delivery.MetaSignup = BrowserMeta
whatsapp.graph_message = browser_send


def browser_media(*args):
    data = io.BytesIO()
    with wave.open(data, 'wb') as writer:
        writer.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        writer.writeframes(b'\0\0' * 16000)
    return data.getvalue(), 'audio/wav'


async def browser_inbound(name, **kwargs):
    assert name == 'handle_message'
    await asyncio.to_thread(process_inbound, kwargs['event_id'])
    return kwargs['_job_id']


media.download = browser_media
media.upload = lambda *args: 'browser-media'
media_intelligence.transcribe = lambda data: ('नमस्ते, आपके काम करने का समय क्या है?', 'hi')
settings.transcription_provider = 'openai'
whatsapp_api.enqueue = browser_inbound


@app.middleware('http')
async def media_worker_fixture(request, call_next):
    response = await call_next(request)
    if request.method == 'POST' and request.url.path.endswith('/media') and response.status_code == 202:
        for _ in range(20):
            if not await asyncio.to_thread(dispatch_agent_reply):
                break
    return response


@app.middleware('http')
async def campaign_worker_fixture(request, call_next):
    response = await call_next(request)
    path = request.url.path.split('/')
    if (request.method == 'POST' and response.status_code < 300 and len(path) == 6
            and path[3] == 'campaigns' and path[5] in ('test', 'start')):
        with Session() as db:
            rows = db.execute(select(CampaignRecipient.id, CampaignRecipient.tenant_id).where(
                CampaignRecipient.campaign_id == path[4], CampaignRecipient.state == 'queued')).all()
        for identity, tenant_id in rows:
            await asyncio.to_thread(campaign_delivery.deliver, identity, tenant_id)
        await asyncio.to_thread(campaign_delivery.maintain)
    return response


@app.middleware('http')
async def error_boundary_fixture(request, call_next):
    if request.url.path == '/api/v1/workspaces' and request.cookies.get('error_boundary_fixture'):
        return JSONResponse(None)  # Exercise a real Next.js server-render failure without production test routes.
    return await call_next(request)


# Browser-only static website responses exercise the real parser and crawl publication.
from app.db import Website  # noqa: E402
from app.services import website_crawl  # noqa: E402


class BrowserWebsite:
    interval = 1

    async def get(self, url, **kwargs):
        path = httpx.URL(url).path
        body = ('User-agent: *\nDisallow: /private' if path == '/robots.txt' else
                '<main><h1>Refunds</h1><p>Refunds accepted within 30 days.</p></main>' if path.endswith('/refunds') else
                '<nav>Menu</nav><main><h1>Help centre</h1><p>Refund and shipping policy.</p>'
                '<a href="/knowledge/refunds">Refunds</a></main><footer>Footer noise</footer>')
        return url, 200, body, {'content-type': 'text/html'}

    async def close(self):
        pass


website_crawl.Fetcher = BrowserWebsite


@app.middleware('http')
async def website_worker_fixture(request, call_next):
    response = await call_next(request)
    if request.method == 'POST' and request.url.path.startswith('/api/v1/websites') and response.status_code == 202:
        with Session() as db:
            sites = db.execute(select(Website.id, Website.tenant_id).where(Website.status == 'queued')).all()
        for identity, tenant_id in sites:
            await website_crawl.crawl(identity, tenant_id)
    return response


from app.services.improvements import process_one as publish_correction  # noqa: E402


@app.middleware('http')
async def improvement_worker_fixture(request, call_next):
    response = await call_next(request)
    if (request.method == 'POST' and request.url.path.startswith('/api/v1/improvements/')
            and request.url.path.endswith('/answer') and response.status_code == 202):
        await asyncio.to_thread(publish_correction)
    return response


@app.middleware('http')
async def appointment_worker_fixture(request, call_next):
    response = await call_next(request)
    if request.method == 'POST' and '/appointments/bookings/' in request.url.path \
            and request.url.path.endswith('/confirm') and response.status_code == 200:
        from app.services.appointment_reminders import process_one
        await asyncio.to_thread(process_one)
    return response


# Calendar OAuth and API calls are entirely local to this test-only ASGI app.
from app.integrations.google_calendar import Client as GoogleClient  # noqa: E402
from app.services import calendar_availability, calendar_sync, google_jobs  # noqa: E402

settings.master_key = 'Y2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2M='
settings.google_calendar_client_id = 'browser-calendar-client'
settings.google_calendar_client_secret = 'browser-calendar-secret'
settings.google_calendar_redirect_uri = 'http://localhost:3000/api/backend/google-calendar/callback'
_calendar_events = {}


def browser_google(self, method, path, **kwargs):
    if 'token' in path:
        return {'access_token': 'browser-access', 'refresh_token': 'browser-refresh', 'expires_in': 3600}
    if 'revoke' in path:
        return {}
    if path == 'users/me/calendarList':
        return {'items': [{'id': 'demo-calendar', 'summary': 'Appointment calendar', 'timeZone': 'Asia/Calcutta',
                           'accessRole': 'owner'}]}
    if path == 'freeBusy':
        return {'calendars': {i['id']: {'busy': []} for i in kwargs['payload']['items']}}
    if method == 'POST':
        value = {**kwargs['payload'], 'etag': '"browser-v1"'}
        _calendar_events[value['id']] = value
        return value
    if method == 'DELETE':
        return _calendar_events.pop(path.rsplit('/', 1)[-1], None)
    return _calendar_events.get(path.rsplit('/', 1)[-1])


GoogleClient.request = browser_google


@app.middleware('http')
async def calendar_worker_fixture(request, call_next):
    response = await call_next(request)
    if (response.status_code < 400 and ('/google-calendar' in request.url.path
            and (request.method != 'GET' or request.url.path.endswith('/callback'))
            or '/appointments/bookings/' in request.url.path and request.method == 'POST')):
        await asyncio.to_thread(calendar_availability.schedule)
        for _ in range(10):
            if (not await asyncio.to_thread(calendar_sync.process_one)
                    and not await asyncio.to_thread(google_jobs.process_one)):
                break
    return response


# Merchant payment operations stay inside this test-only ASGI app.
import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402

from app.integrations.payment_links import Razorpay as PaymentRazorpay  # noqa: E402
from app.models.payments import PaymentRequest  # noqa: E402
from app.services import payment_notifications, payment_processing  # noqa: E402

_payment_links = {}


def browser_payments(self, method, path='', **kwargs):
    if method == 'POST' and not path:
        value = {**kwargs['payload'], 'id': 'plink_' + kwargs['payload']['reference_id'].replace('-', ''),
                 'short_url': 'https://rzp.io/i/browser-demo', 'status': 'created', 'amount_paid': 0}
        _payment_links[value['id']] = value
        return value.copy()
    if path:
        value = _payment_links[path.split('/')[1]]
        if path.endswith('/cancel'):
            value['status'] = 'cancelled'
        return value.copy()
    reference = kwargs.get('params', {}).get('reference_id')
    return {'items': [v.copy() for v in _payment_links.values() if reference and v['reference_id'] == reference]}


PaymentRazorpay.request = browser_payments


@app.post('/api/v1/_test/payments/{identity}/paid', include_in_schema=False)
async def capture_browser_payment(identity: str):
    with Session() as db:
        row = db.get(PaymentRequest, identity)
        connection_id, provider_id = row.connection_id, row.provider_id
    remote = _payment_links[provider_id]
    remote.update(status='paid', amount_paid=remote['amount'])
    raw = json.dumps({'event': 'payment_link.paid', 'payload': {'payment_link': {'entity': remote},
        'payment': {'entity': {'id': 'pay_browser123', 'amount': remote['amount'], 'currency': 'INR',
                              'status': 'captured'}}}}).encode()
    signature = hmac.new(b'browser-payment-webhook-secret', raw, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/payments/webhook/' + connection_id, content=raw,
            headers={'X-Razorpay-Signature': signature, 'X-Razorpay-Event-Id': 'paid-' + identity})
    return JSONResponse(response.json(), status_code=response.status_code)


@app.middleware('http')
async def payment_worker_fixture(request, call_next):
    response = await call_next(request)
    if '/api/v1/payments' in request.url.path and request.method != 'GET' and response.status_code < 400:
        for _ in range(10):
            if not await asyncio.to_thread(payment_processing.process_event) and not await asyncio.to_thread(
                    payment_processing.process_one):
                break
        await asyncio.to_thread(payment_notifications.process_one)
    return response

# Import after the app and earlier fakes are configured; never loaded by production.
# Commerce acceptance uses the real lifecycle, durable sync and knowledge tables.
from app.integrations.commerce import Provider as CommerceProvider  # noqa: E402
from app.services import commerce_sync  # noqa: E402
from tests import browser_accuracy as browser_accuracy  # noqa: E402
from tests import browser_grounding as browser_grounding  # noqa: E402
from tests import browser_http_actions as browser_http_actions  # noqa: E402
from tests import browser_lead_sync as browser_lead_sync  # noqa: E402
from tests import browser_security as browser_security  # noqa: E402
from tests import browser_spreadsheets as browser_spreadsheets  # noqa: E402
from tests import browser_webhooks as browser_webhooks  # noqa: E402


async def commerce_test(self):
    if self.credentials['token'] != 'commerce-browser-token':
        from app.integrations.http_actions.transport import ActionError
        raise ActionError('auth_failed')


async def commerce_products(self):
    return [{'id': 'demo-product', 'title': 'Shipping box', 'description': 'Recycled shipping box',
             'currency': 'INR', 'variants': [{'title': 'Small', 'price': '50.00', 'inventoryQuantity': 3}]}]


commerce_queue = commerce_sync.queue


def commerce_sync_inline(tenant_id, identity):
    commerce_queue(tenant_id, identity)
    commerce_sync.process_one()
    from app.services.commerce import listing
    return listing(tenant_id)[0]


CommerceProvider.test = commerce_test
CommerceProvider.products = commerce_products
commerce_sync.queue = commerce_sync_inline

from tests import browser_order_proof as browser_order_proof  # noqa: E402,F401
