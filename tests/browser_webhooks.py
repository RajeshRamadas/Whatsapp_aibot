"""Deterministic receiver and worker for browser tests; never imported in production."""
import asyncio
import json

from app.main import app
from app.services import webhook_delivery

_fail_next = False
_received = set()


class Receiver:
    async def send(self, url, raw, headers):
        global _fail_next
        assert url == 'https://receiver.example.com/events'
        if _fail_next:
            _fail_next = False
            return 400, 0
        _received.add(json.loads(raw)['id'])
        return 204, 0


@app.post('/api/v1/_test/webhooks/fail-next', include_in_schema=False)
def fail_next():
    global _fail_next
    _fail_next = True
    return {'ok': True}


@app.get('/api/v1/_test/webhooks/received', include_in_schema=False)
def received():
    return {'events': len(_received)}


@app.middleware('http')
async def dispatch(request, call_next):
    response = await call_next(request)
    if request.method in ('POST', 'PATCH') and response.status_code < 400:
        for _ in range(10):
            if not await webhook_delivery.process_one(Receiver()):
                break
        await asyncio.to_thread(webhook_delivery.maintain)
    return response
