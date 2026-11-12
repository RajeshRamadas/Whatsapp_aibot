"""Order lookup tools share the grounding evidence registry; codes bypass the model."""
import json
import re
from pathlib import Path

from . import order_lookup
from .customer_languages import locale_for

COMMAND = re.compile(r'ORDER ([a-f0-9]{12}) ([0-9]{6})', re.I)
COPY = {code: json.loads((Path(__file__).parents[1] / 'messages' / f'ecommerce.{code}.json').read_text())
        for code in ('en', 'hi')}


def copy_for(tenant, question):
    return COPY.get(locale_for(question, tenant.default_language), COPY['en'])


def notice(result, copy):
    if result['state'] == 'awaiting_code':
        return copy['code'].format(reference=result['reference'])
    if result['state'] != 'complete':
        return copy['unavailable']
    data = result['data']
    text = copy['order'].format(number=data['number'], status=data['status'],
                               fulfilment=data.get('fulfilment', data['status']))
    for item in data['items']:
        text += '\n' + str(item['quantity']) + ' × ' + str(item['title'])
    for shipment in data.get('tracking') or []:
        text += '\n' + str(shipment.get('status', ''))
        for tracking in shipment.get('trackingInfo') or []:
            text += '\n' + ' '.join(str(tracking[k]) for k in ('company', 'number', 'url') if tracking.get(k))
    if not data.get('tracking'):
        text += '\n' + copy['noTracking']
    return text if len(text) <= 900 else copy['tooLarge']


def command(tenant, conversation_id, question, enabled):
    match = COMMAND.fullmatch(question.strip())
    if not match:
        return None
    result = (order_lookup.verify(tenant.id, conversation_id, match[1].lower(), match[2])
              if enabled and conversation_id else {'state': 'unavailable'})
    return notice(result, copy_for(tenant, question))

