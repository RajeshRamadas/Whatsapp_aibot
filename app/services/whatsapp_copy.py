"""WhatsApp formatting and localized system replies."""
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from .bot_policy import validate_hours, within_hours

COPY = {
    'en': {
        'greeting': "Hi, I'm {bot}, the automated assistant for {business}.\n"
                    "Ask about hours, services or prices. Type *agent* to reach our team.",
        'unsupported': 'Sorry, I can only read text messages for now. Could you type your question?',
        'rate': "You're sending messages quickly. Please wait a minute and try again.",
        'error': 'Sorry, something went wrong. Please try again or type *agent* to reach our team.',
        'handoff': 'Thanks, our team will reply here as soon as someone is available.',
        'closed': "We're closed now. Our team will reply after {opening}.",
        'unknown': "I don't have that information. Shall I connect you to our team?",
        'yes': 'Yes, connect me', 'no': 'No, thanks', 'team': 'Talk to our team',
        'declined': 'Okay. You can type *agent* anytime to reach our team.',
        'stop': 'Marketing messages are stopped. You can still ask questions. Type *START* to opt back in.',
        'start': 'You are opted in to marketing messages. Type *STOP* to opt out.',
        'from': 'From: ', 'page': 'page', 'more': 'Want the full details?', 'options': 'See options',
    },
    'hi': {
        'greeting': 'नमस्ते, मैं {bot} हूँ, {business} का स्वचालित सहायक।\n'
                    'समय, सेवाओं या कीमतों के बारे में पूछें। टीम से बात करने के लिए *agent* लिखें।',
        'unsupported': 'क्षमा करें, अभी मैं केवल टेक्स्ट पढ़ सकता हूँ। कृपया अपना सवाल लिखें।',
        'rate': 'आप जल्दी-जल्दी संदेश भेज रहे हैं। कृपया एक मिनट बाद फिर कोशिश करें।',
        'error': 'क्षमा करें, कुछ गड़बड़ हुई। फिर कोशिश करें या टीम से बात करने के लिए *agent* लिखें।',
        'handoff': 'धन्यवाद, हमारी टीम उपलब्ध होते ही यहाँ जवाब देगी।',
        'closed': 'अभी हम बंद हैं। हमारी टीम {opening} के बाद जवाब देगी।',
        'unknown': 'मेरे पास यह जानकारी नहीं है। क्या मैं आपको हमारी टीम से जोड़ दूँ?',
        'yes': 'हाँ, जोड़ें', 'no': 'नहीं, धन्यवाद', 'team': 'टीम से बात करें',
        'declined': 'ठीक है। टीम से बात करने के लिए कभी भी *agent* लिखें।',
        'stop': 'मार्केटिंग संदेश बंद हैं। आप सवाल पूछ सकते हैं। इन्हें फिर शुरू करने के लिए *START* लिखें।',
        'start': 'मार्केटिंग संदेश फिर शुरू हैं। इन्हें बंद करने के लिए *STOP* लिखें।',
        'from': 'स्रोत: ', 'page': 'पृष्ठ', 'more': 'क्या आप पूरी जानकारी चाहेंगे?', 'options': 'विकल्प देखें',
    },
}


def copy_for(tenant):
    return COPY['hi' if tenant.default_language.lower().split('-')[0] == 'hi' else 'en']


def handoff_notice(tenant, at):
    copy = copy_for(tenant)
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


def format_reply(text, sources, copy, greeting=''):
    cited = {int(number) for number in re.findall(r'\[([0-9]{1,4})\]', text)}
    text = re.sub(r'[ \t]*\[[0-9]{1,4}\]', '', text)
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
    if len(suffix) > 150:
        suffix = suffix[:149].rstrip() + '…'
    body = '\n\n'.join(part for part in (greeting, text.strip()) if part)
    budget = 600 - len(suffix)
    if len(body) > budget:
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
