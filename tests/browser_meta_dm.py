"""Offline Meta verification, signed webhook input and agent delivery for browser CI."""
import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

import httpx
from fastapi.responses import JSONResponse

from app.api import meta_dm_connections
from app.config import settings
from app.db import now, uid
from app.main import app
from app.services import meta_dm, meta_dm_replies
from app.services.meta_signup import MetaSignupError


class Graph:
    def request(self, path, **kwargs):
        if path.startswith('999'):
            raise MetaSignupError('Synthetic permission failure')
        return {'success': True} if path.endswith('/subscribed_apps') else {'id': path, 'name': 'Meta demo account'}


meta_dm_connections.MetaSignup = Graph
meta_dm_replies.send = lambda *args: 'meta-sent-' + uid()


@app.post('/api/v1/_test/meta-dm/inbound', include_in_schema=False)
async def incoming(body: dict):
    old = bool(body.get('old'))
    data = {'object': body['kind'] if body['kind'] == 'instagram' else 'page', 'entry': [{'messaging': [{
        'sender': {'id': 'meta-expired-customer' if old else 'meta-test-customer'},
        'recipient': {'id': body['external_id']},
        'timestamp': int((now() - timedelta(hours=25 if old else 0)).timestamp() * 1000),
        'message': {'mid': uid(), 'text': 'Could you help with my order?'}}]}]}
    raw = json.dumps(data).encode()
    signature = hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/webhooks/meta-dm', content=raw,
                                     headers={'x-hub-signature-256': 'sha256=' + signature})
    while await asyncio.to_thread(meta_dm.process_one):
        pass
    return JSONResponse(response.json(), status_code=response.status_code)


@app.middleware('http')
async def reply_worker(request, call_next):
    response = await call_next(request)
    if request.method == 'POST' and request.url.path.endswith('/reply') and response.status_code == 200:
        await asyncio.to_thread(meta_dm_replies.dispatch_one)
    return response
