"""Offline HTTP action fixture, imported only by the browser ASGI app."""
import asyncio

from app import llm
from app.main import app
from app.services import http_actions, widget_turns
from tests.browser_app import BrowserKnowledge

_writes, _results = set(), []


class Receiver:
    async def action(self, definition, private, values, identity, *, lookup=False):
        if values.get('sku') == 'FAIL':
            return 503, {'error': 'Synthetic receiver failure'}
        if definition.method != 'GET' and not lookup:
            _writes.add(identity)
        return 200, {'stock': 5, 'private': 'Admin-only synthetic field', 'credential': '[redacted]'}


http_actions.Client = Receiver
_original = BrowserKnowledge.run


def run(self, model, system, messages, tools):
    message = messages[-1]['content']
    if message.startswith(('LOOKUP SKU', 'CHANGE SKU')):
        assert 'UNTRUSTED DATA' in system
        name = 'http_stock' if message.startswith('LOOKUP') else 'http_change_stock'
        tool = next(t for t in tools if t.name == name)
        result = tool.handler({'sku': 'SKU'})
        _results.append(result)
        return llm.ChatResult('Available: ' + str((result.get('untrusted_data') or {}).get('stock', 'pending')), [])
    return _original(self, model, system, messages, tools)


BrowserKnowledge.run = run


@app.get('/api/v1/_test/http-actions', include_in_schema=False)
def metrics():
    return {'writes': len(_writes), 'results': _results}


@app.post('/api/v1/_test/http-actions/reset', include_in_schema=False)
def reset():
    _writes.clear()
    _results.clear()
    return {'ok': True}


@app.middleware('http')
async def action_worker_fixture(request, call_next):
    response = await call_next(request)
    if request.method == 'POST' and '/widget/turns' in request.url.path and response.status_code == 202:
        await asyncio.to_thread(widget_turns.process_turn)
    return response
