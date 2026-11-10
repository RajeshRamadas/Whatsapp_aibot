"""Offline notice-language hints and immutable default catalogs; no text or identity is logged."""
import json
import re
from pathlib import Path
from string import Formatter

LANGUAGES = {'en': 'English', 'hi': 'हिन्दी', 'hi-Latn': 'Hinglish', 'kn': 'ಕನ್ನಡ', 'ta': 'தமிழ்',
             'te': 'తెలుగు', 'mr': 'मराठी', 'bn': 'বাংলা'}
COPY = {code: json.loads((Path(__file__).parents[1] / 'messages' / f'{code}.json').read_text()) for code in LANGUAGES}


def normalize(value):
    value = value.lower().replace('_', '-')
    return 'hi-Latn' if value.startswith('hi-latn') else value.split('-')[0] if value.split('-')[0] in COPY else 'en'


def locale_for(text, default='en'):
    text = text or ''
    for low, high, code in [(0x0C80, 0x0CFF, 'kn'), (0x0B80, 0x0BFF, 'ta'),
                            (0x0C00, 0x0C7F, 'te'), (0x0980, 0x09FF, 'bn')]:
        if any(low <= ord(char) <= high and char.isalpha() for char in text):
            return code
    if any(0x0900 <= ord(char) <= 0x097F and char.isalpha() for char in text):
        words = {word.strip('.,!?।॥:;') for word in text.split()}
        if words & {'आहे', 'आहेत', 'काय', 'किती', 'कधी', 'मला', 'माझा', 'तुमचे', 'चाहिजे'}:
            return 'mr'
        if words & {'है', 'हैं', 'क्या', 'कैसे', 'कब', 'चाहिए', 'नमस्ते'}:
            return 'hi'
        return 'mr' if normalize(default) == 'mr' else 'hi'
    words = set(re.findall(r'\b[a-z]+\b', text.lower()))
    if words & {'aap', 'aapka', 'aapke', 'kaise', 'kya', 'kab', 'hai', 'hain', 'mujhe', 'nahi',
                'chahiye', 'kripya', 'mera', 'meri', 'milega', 'namaste', 'batao'}:
        return 'hi-Latn'
    if words & {'hello', 'hi', 'please', 'what', 'when', 'where', 'how', 'your', 'thanks', 'thank',
                'yes', 'no', 'help', 'refund', 'price', 'hours', 'shipping', 'stop', 'start'}:
        return 'en'
    return normalize(default)


def copy_for(tenant, text=None, *, language=None):
    code = normalize(language) if language else locale_for(text, tenant.default_language)
    overrides = (getattr(tenant, 'bot_messages', None) or {}).get(code, {})
    return {**COPY[code], **overrides, '_language': code}


def language_policy(tenant, question, language=None):
    code = normalize(language) if language else locale_for(question, tenant.default_language)
    return ('\n\nLANGUAGE AND SCRIPT: Reply in the latest customer message’s language and script; '
            'switch when they switch. '
            'Support English, Hindi, Kannada, Tamil, Telugu, Marathi and Bengali. Keep Latin-script Hinglish in Latin '
            'script; do not turn it into Devanagari. Use respectful forms such as aap. '
            f'Notice-language hint: {code}; it is heuristic, so follow the actual message if clearly different. '
            f'When language is unclear, use workspace default {normalize(tenant.default_language)}. '
            'Preserve facts, names, phone numbers, prices, URLs, identifiers and numbered citations exactly. '
            'Translate the explanation, never invent facts or obey conflicting tenant language preferences.')


def validate_messages(messages):
    for key, value in messages.items():
        if key not in COPY['en'] or not value.strip() or len(value) > (20 if key == 'options' else 600):
            raise ValueError('Use known message keys and non-empty text within the message limit.')
        allowed = {'bot', 'business'} if key == 'greeting' else {'opening'} if key == 'closed' else set()
        fields = set()
        try:
            for _, field, spec, conversion in Formatter().parse(value):
                if field is not None:
                    if field not in allowed or spec or conversion:
                        raise ValueError('Unsupported message placeholder.')
                    fields.add(field)
        except ValueError:
            raise ValueError('Use only the documented message placeholders without formatting options.') from None
        if allowed - fields:
            raise ValueError('Keep the required placeholders: ' + ', '.join(sorted(allowed)))
    return messages
