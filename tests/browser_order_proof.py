"""Offline order/transactional-email fixtures, loaded only by the browser test app."""
from app import llm
from app.integrations.commerce import Provider
from app.main import app
from app.services import email, order_lookup
from tests.browser_app import BrowserKnowledge

_run, _stage = BrowserKnowledge.run, email.sender.stage
codes = []


async def order(self, number):
    return {'phones': ['+919999000001'], 'email': 'commerce-customer@example.com', 'number': number,
            'status': 'PAID', 'fulfilment': 'FULFILLED',
            'items': [{'title': 'Private commerce item', 'quantity': 1}], 'tracking': []}


def stage(db, recipient, subject, code):
    if subject == 'Verify order lookup':
        codes.append(code)
        return lambda: None
    return _stage(db, recipient, subject, code)


def run(self, model, system, messages, tools):
    if messages[-1]['content'] == 'ORDER DEMO':
        tool = next(t for t in tools if t.name == 'lookup_order')
        result = tool.handler({'order_number': '1001', 'phone': '919999000001'})
        if result['state'] == 'verified':
            return llm.ChatResult('Order 1001 is PAID [' + str(result['source']['number']) + ']', [])
        return llm.ChatResult('Order details must not be invented', [])
    return _run(self, model, system, messages, tools)


@app.get('/api/v1/_test/order-code', include_in_schema=False)
def latest_code():
    return {'code': codes[-1], 'sent': len(codes)}


Provider.order, BrowserKnowledge.run, email.sender.stage = order, run, stage
order_lookup.delivery_enabled = lambda: True
