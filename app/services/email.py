"""Transactional email: console development backend or encrypted worker outbox."""
import json
import logging
import re
import secrets
from datetime import timedelta

from cryptography.exceptions import InvalidTag
from sqlalchemy import select

from ..config import settings
from ..db import EmailOutbox, EmailSuppression, Session, now, uid
from ..observability import bind
from .crypto import decrypt_system, encrypt_system
from .email_templates import address_hash, normalize, render


def validate_configuration():
    if settings.email_backend == 'console':
        return
    normalize(settings.email_from or '')
    render('Verify your email', '123456', settings.public_app_url)
    if not settings.master_key:
        raise RuntimeError('Email delivery requires MASTER_KEY')
    topic = settings.email_sns_topic_arn or ''
    if (not re.fullmatch(r'arn:aws:sns:[a-z0-9-]+:\d{12}:[A-Za-z0-9_-]+', topic)
            or topic.split(':')[3] != settings.email_region):
        raise RuntimeError('Configure the email feedback SNS topic in EMAIL_REGION')
    if settings.environment == 'production' and not settings.public_app_url.startswith('https://'):
        raise RuntimeError('PUBLIC_APP_URL must use HTTPS in production')

class EmailSender:
    def send(self, recipient: str, subject: str, code: str) -> None:
        raise NotImplementedError

    def stage(self, db, recipient, subject, code):
        return lambda: self.send(recipient, subject, code)


class ConsoleEmailSender(EmailSender):
    def send(self, recipient: str, subject: str, code: str) -> None:
        logging.getLogger(__name__).info('email.queued')


class QueuedEmailSender(EmailSender):
    def stage(self, db, recipient, subject, code):
        validate_configuration()
        text, html, minutes = render(subject, code, settings.public_app_url)
        recipient = normalize(recipient)
        identity, digest = uid(), address_hash(recipient)
        bind(job_id=identity)
        suppressed = db.get(EmailSuppression, digest) is not None
        payload = json.dumps({'recipient': recipient, 'subject': subject, 'text': text, 'html': html}).encode()
        db.add(EmailOutbox(id=identity, recipient_hash=digest,
                           payload_enc=None if suppressed else encrypt_system('email', identity, payload),
                           status='suppressed' if suppressed else 'queued',
                           expires_at=now() + timedelta(minutes=minutes)))
        return lambda: None

    def send(self, recipient, subject, code):
        with Session.begin() as db:
            self.stage(db, recipient, subject, code)


class ConfiguredEmailSender(EmailSender):
    def backend(self):
        return QueuedEmailSender() if settings.email_backend == 'ses' else ConsoleEmailSender()

    def stage(self, db, recipient, subject, code):
        return self.backend().stage(db, recipient, subject, code)

    def send(self, recipient, subject, code):
        self.backend().send(recipient, subject, code)


sender: EmailSender = ConfiguredEmailSender()


class DeliveryError(RuntimeError):
    def __init__(self, permanent=False, retry_after=0):
        super().__init__('Email provider could not complete delivery')
        self.permanent, self.retry_after = permanent, retry_after


def deliver_one(transport):
    """Claim a leased outbox row; never hold a DB connection over provider calls."""
    with Session.begin() as db:
        row = db.scalar(select(EmailOutbox).where(
            EmailOutbox.status.in_(('queued', 'sending')), EmailOutbox.next_attempt_at <= now(),
        ).order_by(EmailOutbox.created_at, EmailOutbox.id).limit(1).with_for_update(skip_locked=True))
        if row is None:
            return False
        if row.expires_at <= now() or row.attempts >= 5 or db.get(EmailSuppression, row.recipient_hash):
            row.status = 'suppressed' if db.get(EmailSuppression, row.recipient_hash) else 'failed'
            row.payload_enc = None
            return True
        row.status, row.attempts = 'sending', row.attempts + 1
        row.next_attempt_at = now() + timedelta(minutes=2)
        identity, payload, attempt = row.id, row.payload_enc, row.attempts
        bind(job_id=identity)
    try:
        message = json.loads(decrypt_system('email', identity, payload))
        provider_id = transport.send(message)
    except Exception as exc:
        with Session.begin() as db:
            current = db.get(EmailOutbox, identity, with_for_update=True)
            if current and current.status == 'sending' and current.attempts == attempt:
                permanent = isinstance(exc, (ValueError, InvalidTag)) or getattr(exc, 'permanent', False)
                current.status = 'failed' if attempt >= 5 or permanent else 'queued'
                current.next_attempt_at = now() + timedelta(seconds=max(5 * 2 ** attempt + secrets.randbelow(5),
                                                                         getattr(exc, 'retry_after', 0)))
                if current.status == 'failed':
                    current.payload_enc = None
        return True
    with Session.begin() as db:
        current = db.get(EmailOutbox, identity, with_for_update=True)
        if current and current.status == 'sending' and current.attempts == attempt:
            current.status, current.provider_id, current.payload_enc = 'sent', provider_id, None
    return True
