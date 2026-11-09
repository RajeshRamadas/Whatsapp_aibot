import base64
import json
from datetime import timedelta

import pytest
from botocore.exceptions import ClientError
from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from app.api import email_feedback
from app.config import settings
from app.db import EmailFeedbackEvent, EmailOutbox, EmailSuppression, Session, User, engine, now, uid
from app.integrations import ses
from app.services import crypto, email, email_templates

CERT_URL = 'https://sns.ap-south-1.amazonaws.com/SimpleNotificationService-' + 'a' * 32 + '.pem'


@pytest.fixture
def configured(client, monkeypatch):
    for key, value in dict(email_backend='ses', email_from='notifications@example.com', email_region='ap-south-1',
                           email_sns_topic_arn='arn:aws:sns:ap-south-1:123456789012:email-feedback',
                           public_app_url='https://app.example.com', master_key=base64.b64encode(b'k' * 32).decode(),
                           master_key_version=1, master_key_previous={}).items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(email, 'sender', email.ConfiguredEmailSender())
    email_feedback.certificate_limiter.clear()
    with ses.redis_client() as redis:
        redis.delete(ses.certificate_key(CERT_URL))
    return client


class FakeSES:
    def __init__(self, error=None):
        self.messages, self.error = [], error

    def send(self, message):
        assert engine.pool.checkedout() == 0  # No transaction spans provider I/O.
        self.messages.append(message)
        if self.error:
            raise self.error
        return 'fake-message-id'


def outbox():
    with Session() as db:
        return db.scalars(select(EmailOutbox).order_by(EmailOutbox.created_at, EmailOutbox.id)).all()


def test_signup_queues_encrypted_email_atomically_and_worker_delivers(configured, monkeypatch, caplog):
    request = {'email': 'hello@example.com', 'password': 'strong-unique-password', 'name': 'Test'}
    with caplog.at_level('INFO'):
        assert configured.post('/api/v1/auth/signup', json=request).status_code == 201
        row = outbox()[0]
        assert b'hello@example.com' not in row.payload_enc
        transport = FakeSES()
        assert email.deliver_one(transport)
    message = transport.messages[0]
    code = message['text'].split('\n\n')[2]
    assert len(code) == 6 and code.isdigit()
    verified = configured.post('/api/v1/auth/verify-email', json={'email': request['email'], 'code': code})
    assert verified.status_code == 200
    assert outbox()[0].payload_enc is None and outbox()[0].provider_id == 'fake-message-id'
    assert not email.deliver_one(transport)
    assert code not in caplog.text and request['email'] not in caplog.text
    monkeypatch.setattr(email, 'encrypt_system', lambda *args: (_ for _ in ()).throw(ValueError('key failure')))
    with pytest.raises(ValueError):
        configured.post('/api/v1/auth/signup', json={**request, 'email': 'rollback@example.com'})
    with Session() as db:
        assert db.scalar(select(User).where(User.email == 'rollback@example.com')) is None
    assert len(outbox()) == 1


def test_retries_expiry_suppression_and_crypto_binding(configured, monkeypatch):
    sender = email.QueuedEmailSender()
    sender.send('retry@example.com', 'Reset your password', '<code&>')
    row = outbox()[0]
    data = crypto.decrypt_system('email', row.id, row.payload_enc)
    assert '<code&>' in json.loads(data)['text'] and '&lt;code&amp;&gt;' in json.loads(data)['html']
    for purpose, identity in [('other', row.id), ('email', uid())]:
        with pytest.raises(InvalidTag):
            crypto.decrypt_system(purpose, identity, row.payload_enc)
    monkeypatch.setattr(settings, 'master_key_previous', {'1': settings.master_key})
    monkeypatch.setattr(settings, 'master_key_version', 2)
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b'n' * 32).decode())
    assert crypto.decrypt_system('email', row.id, row.payload_enc) == data
    transport = FakeSES(email.DeliveryError(retry_after=60))
    assert email.deliver_one(transport)
    assert outbox()[0].status == 'queued' and outbox()[0].next_attempt_at > now() + timedelta(seconds=55)
    assert not email.deliver_one(transport)
    for attempt in range(2, 6):
        with Session.begin() as db:
            db.get(EmailOutbox, row.id).next_attempt_at = now() - timedelta(seconds=1)
        assert email.deliver_one(transport)
        assert outbox()[0].attempts == attempt
    assert outbox()[0].status == 'failed' and outbox()[0].payload_enc is None
    sender.send('expired@example.com', 'Verify your email', '123456')
    with Session.begin() as db:
        db.get(EmailOutbox, outbox()[-1].id).expires_at = now() - timedelta(seconds=1)
    assert email.deliver_one(FakeSES())
    sender.send('permanent@example.com', 'Verify your email', '123456')
    assert email.deliver_one(FakeSES(email.DeliveryError(permanent=True)))
    sender.send('tampered@example.com', 'Verify your email', '123456')
    with Session.begin() as db:
        last = db.get(EmailOutbox, outbox()[-1].id)
        last.payload_enc = last.payload_enc[:-1] + bytes([last.payload_enc[-1] ^ 1])
    assert email.deliver_one(FakeSES())
    assert all(row.status == 'failed' and row.payload_enc is None for row in outbox())


@pytest.fixture
def signed(configured):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SNS test fixture')])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(1).not_valid_before(now() - timedelta(days=1))
                   .not_valid_after(now() + timedelta(days=1)).sign(key, hashes.SHA256()))
    with ses.redis_client() as redis:
        redis.set(ses.certificate_key(CERT_URL), certificate.public_bytes(serialization.Encoding.PEM), ex=60)

    def sign(payload, **overrides):
        message = dict(Type='Notification', MessageId=uid(), TopicArn=settings.email_sns_topic_arn,
                       Message=json.dumps(payload), Timestamp=now().isoformat(), SigningCertURL=CERT_URL,
                       SignatureVersion='2') | overrides
        fields = ['Message', 'MessageId', 'Timestamp', 'TopicArn', 'Type']
        fields += ['SubscribeURL', 'Token'] if message['Type'] == 'SubscriptionConfirmation' else []
        canonical = ''.join(f'{field}\n{message[field]}\n' for field in sorted(fields)).encode()
        message['Signature'] = base64.b64encode(key.sign(canonical, padding.PKCS1v15(), hashes.SHA256())).decode()
        return message
    return sign


@pytest.mark.parametrize('kind,field', [('Bounce', 'bouncedRecipients'), ('Complaint', 'complainedRecipients')])
def test_signed_feedback_deduplicates_and_suppresses_pending_and_future_mail(configured, signed, kind, field):
    email.sender.send('suppressed@example.com', 'Verify your email', '123456')
    message = signed({'notificationType': kind, kind.lower(): {field: [{'emailAddress': 'SUPPRESSED@example.com'}]}})
    for _ in range(2):
        assert configured.post('/webhooks/email', json=message).status_code == 200
    email.sender.send('suppressed@example.com', 'Reset your password', '654321')
    assert all(row.status == 'suppressed' and row.payload_enc is None for row in outbox())
    assert not email.deliver_one(FakeSES())
    with Session() as db:
        assert len(db.scalars(select(EmailFeedbackEvent)).all()) == 1
        assert db.get(EmailSuppression, email_templates.address_hash('suppressed@example.com')).reason == kind.lower()


def test_feedback_auth_validation_and_certificate_miss(configured, signed, monkeypatch):
    message = signed({'notificationType': 'Delivery'})
    assert configured.post('/webhooks/email', json={**message, 'TopicArn': 'other'}).status_code == 403
    assert configured.post('/webhooks/email', json={**message, 'Message': '{}'}).status_code == 401
    assert configured.post('/webhooks/email', json=[]).status_code == 422
    assert configured.post('/webhooks/email', json=signed({'notificationType': 'Bounce'})).status_code == 422
    for url in ['http://127.0.0.1/secret', CERT_URL + '?redirect=1', CERT_URL.replace('.com/', '.com.evil/')]:
        assert configured.post('/webhooks/email', json={**message, 'SigningCertURL': url}).status_code == 401
    with ses.redis_client() as redis:
        redis.delete(ses.certificate_key(CERT_URL))
    jobs = []

    async def enqueue(name, **kwargs):
        jobs.append((name, kwargs))

    monkeypatch.setattr(email_feedback, 'enqueue', enqueue)
    monkeypatch.setattr(ses.httpx, 'get', lambda *a, **kw: pytest.fail('HTTP in request handler'))
    response = configured.post('/webhooks/email', json=message)
    assert response.status_code == 503 and response.headers['Retry-After'] == '10'
    assert jobs[0][0] == 'cache_email_certificate' and jobs[0][1]['url'] == CERT_URL
    monkeypatch.setattr(settings, 'email_backend', 'console')
    assert configured.post('/webhooks/email', json=message).status_code == 404


def test_confirmation_and_ses_sdk_arguments(configured, signed, monkeypatch):
    message = signed({}, Type='SubscriptionConfirmation', Token='secret-token', SubscribeURL='https://unused.example')
    assert configured.post('/webhooks/email', json=message).status_code == 200
    assert b'secret-token' not in outbox()[0].payload_enc
    calls = []

    class SDK:
        def confirm_subscription(self, **kwargs):
            calls.append(kwargs)

        def send_email(self, **kwargs):
            calls.append(kwargs)
            return {'MessageId': 'ses-message-id'}

    monkeypatch.setattr(ses, 'client', lambda service: SDK())
    assert email.deliver_one(ses.Transport())
    assert calls[0]['Token'] == 'secret-token' and calls[0]['TopicArn'] == settings.email_sns_topic_arn
    for subject in email_templates.TEMPLATES:
        email.sender.send('recipient@example.com', subject, 'value')
        assert email.deliver_one(ses.Transport())
        body = calls[-1]['Content']['Simple']['Body']
        assert body['Html']['Data'] and body['Text']['Data']
        assert calls[-1]['FromEmailAddress'] == settings.email_from


@pytest.mark.parametrize('status,permanent', [(429, False), (503, False), (400, True)])
def test_ses_provider_failures_have_bounded_safe_retry_metadata(configured, monkeypatch, status, permanent):
    class SDK:
        def send_email(self, **kwargs):
            raise ClientError({'Error': {'Code': 'Rejected', 'Message': 'private body'}, 'ResponseMetadata': {
                'HTTPStatusCode': status, 'HTTPHeaders': {'retry-after': '60'}}}, 'SendEmail')
    monkeypatch.setattr(ses, 'client', lambda service: SDK())
    with pytest.raises(email.DeliveryError) as error:
        ses.Transport().send({'subject': 'Verify your email', 'recipient': 'a@example.com', 'text': 'x', 'html': 'x'})
    assert error.value.permanent == permanent and error.value.retry_after == 60
    assert 'private' not in str(error.value)
