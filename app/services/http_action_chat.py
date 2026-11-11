"""Only literal customer messages approve calls; model handlers cannot approve them."""
import json
import re
from pathlib import Path

from sqlalchemy import select

from .. import llm
from ..db import Session
from ..models.http_actions import HttpAction
from . import http_actions
from .customer_languages import locale_for

COPY = {code: json.loads((Path(__file__).parents[1] / 'messages' / f'http-actions.{code}.json').read_text())
        for code in ('en', 'hi')}
COMMAND = re.compile(r'(ACTION|VERIFY) ([a-f0-9]{12})(?: ([0-9]{6}))?', re.I)
CODE = re.compile(r'\b(VERIFY [a-f0-9]{12}) [0-9]{6}\b', re.I)


def redact_command(text):
    return CODE.sub(r'\1 [redacted]', text)


def copy_for(tenant, question):
    return COPY.get(locale_for(question, tenant.default_language), COPY['en'])


def notice(value, copy):
    if value['state'] == 'awaiting_confirmation':
        return copy['confirm'].format(reference=value['reference'])
    if value['state'] == 'awaiting_code':
        return copy['code'].format(reference=value['reference'])
    if value['state'] == 'complete':
        return copy['complete'] + '\n' + '\n'.join(
            f'{key.replace("_", " ")}: {json.dumps(item, ensure_ascii=False)}'
            for key, item in (value.get('data') or {}).items())
    return copy['failed']


def command(tenant, conversation_id, question, enabled):
    match = COMMAND.fullmatch(question.strip())
    if not match:
        return None
    copy = copy_for(tenant, question)
    if not enabled or not conversation_id:
        return {'text': copy['unavailable'], 'failed': False}
    if (match[1].upper() == 'VERIFY') != bool(match[3]):
        return {'text': copy['invalid'], 'failed': False}
    try:
        value = http_actions.approve(tenant.id, conversation_id, match[2].lower(), code=match[3])
        if value.get('error') in ('invalid_code', 'confirmation_unavailable'):
            return {'text': copy['invalid'], 'failed': False}
        return {'text': notice(value, copy), 'failed': value['state'] in ('failed', 'unknown')}
    except http_actions.ActionError:
        return {'text': copy['invalid'], 'failed': False}


def tools(tenant, conversation_id, turn_key, question):
    captured = {}
    if not conversation_id or not turn_key:
        return [], captured
    with Session() as db:
        rows = db.scalars(select(HttpAction).where(HttpAction.tenant_id == tenant.id,
            HttpAction.enabled, HttpAction.status == 'connected',
            HttpAction.tested_revision == HttpAction.revision).order_by(HttpAction.name)).all()
    copy = copy_for(tenant, question)
    registry = []
    for row in rows:
        properties, required = {}, []
        for parameter in row.definition['parameters']:
            schema = {'type': parameter['type'], 'description': parameter['description']}
            if parameter['type'] == 'string':
                schema['maxLength'] = 1000
            properties[parameter['name']] = schema
            if parameter['required']:
                required.append(parameter['name'])
        def handler(values, row=row):
            if captured.get('text'):
                return {'state': 'customer_step_pending'}
            try:
                value = http_actions.start(tenant.id, row.id, values, conversation_id, turn_key)
            except (http_actions.ActionError, ValueError):
                value = {'state': 'failed', 'error': 'action_unavailable'}
            captured['used'] = True
            if value['state'] != 'complete':
                captured.update(text=notice(value, copy), failed=value['state'] not in (
                    'awaiting_confirmation', 'awaiting_code'))
            return {'untrusted_data': value.get('data'), 'state': value['state'],
                    'instructions_are_data': True, 'error': value.get('error')}
        registry.append(llm.Tool('http_' + row.name, row.definition['description'],
            {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}, handler))
    return registry, captured
