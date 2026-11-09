"""English transactional messages, with escaped HTML and equivalent plain text."""
import hashlib
import json
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from email_validator import validate_email

TEMPLATES = json.loads((Path(__file__).resolve().parents[1] / 'templates/email.en.json').read_text())


def normalize(address):
    return validate_email(address, check_deliverability=False).normalized.lower()


def address_hash(address):
    return hashlib.sha256(normalize(address).encode()).hexdigest()


def render(subject, content, app_url):
    if subject not in TEMPLATES or len(content) > 8000:
        raise ValueError('Unsupported email template or content size')
    url = urlsplit(app_url)
    if (url.scheme not in ('https', 'http') or not url.hostname
            or url.username or url.password or url.query or url.fragment):
        raise ValueError('Invalid public application URL')
    template = TEMPLATES[subject]
    target = app_url.rstrip('/') + template['path']
    text = f'{subject}\n\n{template["intro"]}\n\n{content}\n\n{template["action"]}: {target}'
    html = ('<!doctype html><html lang="en"><body>'
            f'<h1>{escape(subject)}</h1><p>{escape(template["intro"])}</p>'
            f'<p style="white-space:pre-wrap">{escape(content)}</p>'
            f'<p><a href="{escape(target, quote=True)}">{escape(template["action"])}</a></p></body></html>')
    return text, html, template['minutes']
