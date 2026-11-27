"""Offline Meta verification, signed webhook input and agent delivery for browser CI."""
import asyncio
import hashlib
import hmac
import json
from datetime import timedelta
from io import BytesIO

import httpx
from fastapi.responses import JSONResponse
from PIL import Image, ImageDraw

from app.api import meta_dm_connections
from app.config import settings
from app.db import now, uid
from app.main import app
from app.services import meta_bot, meta_dm, meta_dm_replies, meta_media, meta_oauth
from app.services.meta_signup import MetaSignupError


class Graph:
    def request(self, path, **kwargs):
        if path == 'oauth/access_token':
            return {'access_token': 'synthetic-meta-oauth-token'}
        if path == 'me/accounts':
            return {'data': [{'id': '888100', 'name': 'Demo Facebook Page', 'access_token': 'synthetic-page-token',
                             'instagram_business_account': {'id': '888111', 'name': 'Demo Instagram'}}]}
        if path == 'debug_token':
            return {'data': {'is_valid': True, 'app_id': settings.whatsapp_app_id}}
        if path.startswith('999'):
            raise MetaSignupError('Synthetic permission failure')
        if path.endswith('/subscribed_apps'):
            return {'success': True} if kwargs.get('payload') is not None or kwargs.get('method') else {'data': []}
        marker = 'ig_id' if 'ig_id' in (kwargs.get('params') or {}).get('fields', '') else 'category'
        return {'id': path, 'name': 'Demo Instagram' if path == '888111' else 'Meta demo account',
                marker: path if marker == 'ig_id' else 'Business'}


meta_dm_connections.MetaSignup = Graph
meta_oauth.MetaSignup = Graph
settings.meta_dm_oauth_redirect_uri = 'http://localhost:3000/api/backend/meta-dm/oauth/callback'
settings.whatsapp_app_id = settings.whatsapp_app_id or '123'
meta_dm_replies.send = lambda *args: 'meta-sent-' + uid()


async def download_fixture(url, kind):
    if 'unavailable' in url:
        raise ValueError('Synthetic expired attachment')
    if kind == 'image':
        image = Image.new('RGB', (240, 120), '#243c5a')
        ImageDraw.Draw(image).text((22, 50), 'Sample customer image', fill='white')
        output = BytesIO()
        image.save(output, format='PNG')
        return output.getvalue(), 'image/png'
    if kind == 'audio':
        from tests.browser_app import browser_media
        return browser_media()
    return b'%PDF-1.7\nPrivate Meta browser fixture', 'application/pdf'


meta_media.download = download_fixture


@app.post('/api/v1/_test/meta-dm/inbound', include_in_schema=False)
async def incoming(body: dict):
    old = bool(body.get('old'))
    data = {'object': body['kind'] if body['kind'] == 'instagram' else 'page', 'entry': [{'messaging': [{
        'sender': {'id': 'meta-expired-customer' if old else 'meta-test-customer'},
        'recipient': {'id': body['external_id']},
        'timestamp': int((now() - timedelta(hours=25 if old else 0)).timestamp() * 1000),
        'message': {'mid': uid(), 'text': body.get('text', 'Could you help with my order?'),
                    **({'attachments': body['attachments']} if 'attachments' in body else {})}}]}]}
    raw = json.dumps(data).encode()
    signature = hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/webhooks/meta-dm', content=raw,
                                     headers={'x-hub-signature-256': 'sha256=' + signature})
    while await asyncio.to_thread(meta_dm.process_one):
        pass
    while await asyncio.to_thread(meta_media.process_one):
        pass
    for _ in range(10):
        if not await asyncio.to_thread(meta_bot.process_one):
            break
    while await asyncio.to_thread(meta_dm_replies.dispatch_one):
        pass
    return JSONResponse(response.json(), status_code=response.status_code)


@app.middleware('http')
async def reply_worker(request, call_next):
    response = await call_next(request)
    if request.method == 'POST' and request.url.path.endswith('/reply') and response.status_code == 200:
        await asyncio.to_thread(meta_dm_replies.dispatch_one)
    if '/api/v1/meta-dm/oauth/' in request.url.path and response.status_code < 400:
        for _ in range(3):
            if not await asyncio.to_thread(meta_oauth.process_one):
                break
    return response


@app.post('/api/v1/_test/meta-dm/receipt', include_in_schema=False)
async def receipt_fixture(body: dict):
    from sqlalchemy import select

    from app.db import Channel, Conversation, Message, Session
    from app.services import meta_receipts
    with Session() as db:
        mids = db.scalars(select(Message.channel_message_id).join(Conversation).join(
            Channel, Channel.id == Conversation.meta_channel_id).where(Channel.external_id == body['external_id'],
            Channel.kind == body['kind'], Message.direction == 'outbound', Message.channel_message_id.is_not(None))
            .order_by(Message.id)).all()
    detail = {'mid': mids[-1]} if body['kind'] == 'instagram' else {
        'watermark': int(now().timestamp() * 1000), **({'mids': mids} if body['status'] == 'delivered' else {})}
    data = {'object': 'instagram' if body['kind'] == 'instagram' else 'page', 'entry': [{'messaging': [{
        'sender': {'id': 'meta-test-customer'}, 'recipient': {'id': body['external_id']},
        'read' if body['status'] == 'read' else 'delivery': detail}]}]}
    raw = json.dumps(data).encode()
    signature = hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/webhooks/meta-dm', content=raw,
            headers={'x-hub-signature-256': 'sha256=' + signature})
    for _ in range(20):
        if not await asyncio.to_thread(meta_receipts.process_one):
            break
    return JSONResponse(response.json(), status_code=response.status_code)
