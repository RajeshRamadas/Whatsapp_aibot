"""WhatsApp formatting and localized system replies."""
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from .bot_policy import validate_hours, within_hours
from .customer_languages import COPY as COPY
from .customer_languages import copy_for


def handoff_notice(tenant, at, copy=None):
    copy = copy or copy_for(tenant)
    if within_hours(tenant, at):
        return copy['handoff']
    local = at.astimezone(ZoneInfo(tenant.timezone))
    schedule = validate_hours(tenant.business_hours)
    for offset in range(8):
        day = local.date() + timedelta(days=offset)
        window = schedule.get(str(day.weekday()))
        if window and (offset or window[0] > local.strftime('%H:%M')):
            return copy['closed'].format(opening=f'{day.isoformat()} {window[0]} ({tenant.timezone})')
    return copy['handoff']


def format_reply(text, sources, copy, greeting='', *, truncate=True):
    groups = re.findall(r'\[([0-9]{1,4}(?:\s*,\s*[0-9]{1,4})*)\]', text)
    cited = {int(number.strip()) for group in groups for number in group.split(',')}
    text = re.sub(r'[ \t]*\[[0-9]{1,4}(?:\s*,\s*[0-9]{1,4})*\]', '', text)
    text = re.sub(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)', r'\1 (\2)', text)
    text = re.sub(r'^\s{0,3}#{1,6}\s+(.+)$', r'*\1*', text, flags=re.M)
    text = re.sub(r'\*\*(.+?)\*\*|__(.+?)__', lambda match: '*' + (match[1] or match[2]) + '*', text)
    text = re.sub(r'^([ \t]*)[-*]\s+', r'\1• ', text, flags=re.M)
    text = re.sub(r'^\s*\|(?:\s*:?-+:?\s*\|)+\s*$', '', text, flags=re.M)
    text = re.sub(r'^\s*\|(.+)\|\s*$', lambda match: ' · '.join(part.strip() for part in match[1].split('|')),
                  text, flags=re.M)
    labels = []
    for source in sources:
        if source['number'] not in cited:
            continue
        label = source['filename']
        if source.get('page') is not None:
            label += f", {copy['page']} {source['page']}"
        if label not in labels:
            labels.append(label)
    suffix = '\n\n' + copy['from'] + '; '.join(labels) if labels else ''
    # Reserve space for a short citation line and a follow-up invitation.
    if truncate and len(suffix) > 150:
        suffix = suffix[:149].rstrip() + '…'
    body = '\n\n'.join(part for part in (greeting, text.strip()) if part)
    budget = 600 - len(suffix)
    if len(body) > budget:
        if not truncate:
            raise ValueError('Verified reply would require truncation')
        short = body[:budget - len(copy['more']) - 3].rsplit(maxsplit=1)[0]
        body = short.rstrip() + '…\n' + copy['more']
    return body + suffix


def interactive_payload(text, choices, copy):
    if not choices:
        return None
    if len(choices) <= 3 and all(len(title) <= 20 for _, title in choices):
        return {'type': 'button', 'body': {'text': text}, 'action': {'buttons': [
            {'type': 'reply', 'reply': {'id': key, 'title': title}} for key, title in choices
        ]}}
    return {'type': 'list', 'body': {'text': text}, 'action': {
        'button': copy['options'], 'sections': [{'rows': [
            {'id': key, 'title': title[:24]} for key, title in choices[:10]
        ]}],
    }}
