"""Meta template synchronization and send eligibility."""
import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..db import Channel, Contact, Session, Template
from .contacts import marketing_eligible
from .meta_signup import MetaSignup, MetaSignupError

VARIABLE = re.compile(r'\{\{([1-9][0-9]*)\}\}')


def variable_count(body):
    keys = {match[1] for match in VARIABLE.finditer(body)}
    if len(keys) > 20 or keys != {str(index) for index in range(1, len(keys) + 1)}:
        raise ValueError('Use consecutive numbered variables starting at {{1}}, up to 20')
    if '{{' in VARIABLE.sub('', body) or '}}' in VARIABLE.sub('', body):
        raise ValueError('Only numbered body variables are supported')
    return len(keys)


def render(body, values):
    if len(values) != variable_count(body):
        raise ValueError('Fill every template variable')
    return VARIABLE.sub(lambda match: values[int(match[1]) - 1], body)


def list_meta(waba, token):
    graph = MetaSignup()
    params = {'fields': 'id,name,language,category,status,components', 'limit': 100}
    rows = []
    for _ in range(50):
        result = graph.request(f'{waba}/message_templates', token=token, params=params)
        rows.extend(result.get('data', []))
        paging = result.get('paging') or {}
        if not paging.get('next'):
            return rows
        cursor = (paging.get('cursors') or {}).get('after')
        if not cursor:
            break
        params['after'] = cursor
    raise MetaSignupError('Meta returned an incomplete template list')


def upsert(db, channel_id, row):
    components = row.get('components') or []
    body = None
    if len(components) == 1 and components[0].get('type') == 'BODY':
        candidate = components[0].get('text')
        if isinstance(candidate, str) and candidate:
            try:
                variable_count(candidate)
                body = candidate
            except ValueError:
                pass
    fields = {'name': row['name'], 'language': row['language'], 'category': row.get('category', 'UTILITY'),
              'status': row.get('status', 'PENDING').upper(), 'body': body}
    statement = insert(Template).values(channel_id=channel_id, meta_id=str(row['id']), **fields)
    db.execute(statement.on_conflict_do_update(index_elements=['channel_id', 'meta_id'], set_=fields))


def eligible_contact(db, conversation):
    contact = db.scalar(select(Contact).where(Contact.id == conversation.contact_id,
        Contact.tenant_id == conversation.tenant_id)) if conversation.contact_id else None
    return bool(contact and contact.tenant_id == conversation.tenant_id and marketing_eligible(contact))


def template_for_send(db, channel_id, template_id):
    return db.scalar(select(Template).where(
        Template.id == template_id, Template.channel_id == channel_id,
        Template.status == 'APPROVED', Template.body.is_not(None),
    ))


def send_payload(template, values):
    payload = {'name': template.name, 'language': {'code': template.language}}
    if values:
        payload['components'] = [{'type': 'body', 'parameters': [
            {'type': 'text', 'text': value} for value in values
        ]}]
    return payload


def record_status(waba_id, meta_id, status):
    with Session.begin() as db:
        row = db.scalar(select(Template).join(Channel, Channel.id == Template.channel_id).where(
            Channel.waba_id == waba_id, Channel.kind == 'whatsapp', Template.meta_id == meta_id,
        ).with_for_update(of=Template))
        if row is not None:
            row.status = status
