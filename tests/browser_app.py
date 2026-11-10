"""ASGI app for browser CI with an isolated Razorpay fake."""
import asyncio
import io
import wave
from datetime import timedelta

import httpx
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import whatsapp
from app.api import auth, billing, templates
from app.api import whatsapp as whatsapp_api
from app.config import settings
from app.db import CampaignRecipient, Channel, Session, Template, now, uid
from app.invoice_config import InvoiceIssuer
from app.jobs.worker import dispatch_agent_reply, process_inbound
from app.main import app
from app.rate_limits import RateLimiter
from app.services import campaign_delivery, media, media_intelligence
from app.services import templates as template_service
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
