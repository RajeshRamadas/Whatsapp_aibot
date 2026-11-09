"""SES delivery and SNS authentication. Provider I/O runs in workers only."""
import base64
import hashlib
import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from redis import Redis
from sqlalchemy import case, update
from sqlalchemy.dialects.postgresql import insert

from ...config import settings
from ...db import EmailFeedbackEvent, EmailOutbox, EmailSuppression, Session, now, uid
from ...observability import integration
from ...services.crypto import encrypt_system
from ...services.email import DeliveryError
from ...services.email_templates import address_hash


def client(service):
    return boto3.client(service, region_name=settings.email_region,
                        config=Config(connect_timeout=5, read_timeout=15, retries={'total_max_attempts': 1}))


class Transport:
    @integration('ses')
    def send(self, message):
        try:
            if message.get('action') == 'confirm':
                client('sns').confirm_subscription(TopicArn=settings.email_sns_topic_arn,
                                                   Token=message['token'], AuthenticateOnUnsubscribe='true')
                return 'confirmed'
            content = {'Subject': {'Data': message['subject'], 'Charset': 'UTF-8'}, 'Body': {
                'Text': {'Data': message['text'], 'Charset': 'UTF-8'},
                'Html': {'Data': message['html'], 'Charset': 'UTF-8'}}}
            extra = ({'ConfigurationSetName': settings.email_configuration_set}
                     if settings.email_configuration_set else {})
            result = client('sesv2').send_email(FromEmailAddress=settings.email_from,
                                               Destination={'ToAddresses': [message['recipient']]},
                                               Content={'Simple': content}, **extra)
            return result['MessageId']
        except ClientError as exc:
            metadata = exc.response.get('ResponseMetadata', {})
            retry = str(metadata.get('HTTPHeaders', {}).get('retry-after', '0'))
            delay = min(int(retry), 3600) if re.fullmatch(r'\d{1,4}', retry) else 0
            code = exc.response.get('Error', {}).get('Code')
            status = metadata.get('HTTPStatusCode', 500)
            transient = status >= 500 or status in (408, 429) or code in (
                'TooManyRequestsException', 'Throttling', 'ThrottlingException', 'ServiceUnavailableException')
            raise DeliveryError(permanent=not transient, retry_after=delay) from None


def certificate_key(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != f'sns.{settings.email_region}.amazonaws.com'
            or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.query or parsed.fragment
            or not re.fullmatch(r'/SimpleNotificationService-[a-fA-F0-9]{32,64}\.pem', parsed.path)):
        raise ValueError('Untrusted SNS signing certificate URL')
    return 'email:sns:cert:' + hashlib.sha256(url.encode()).hexdigest()


def redis_client():
    return Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)


def load_certificate(pem):
    cert = x509.load_pem_x509_certificate(pem)
    if not cert.not_valid_before_utc <= now() <= cert.not_valid_after_utc:
        raise ValueError('SNS certificate is outside its validity period')
    if not isinstance(cert.public_key(), rsa.RSAPublicKey):
        raise ValueError('SNS requires an RSA signing certificate')
    return cert.public_key()


@integration('sns_certificate')
def cache_certificate(url):
    key = certificate_key(url)
    response = httpx.get(url, timeout=10, follow_redirects=False)
    response.raise_for_status()
    if len(response.content) > 32768:
        raise ValueError('SNS certificate too large')
    load_certificate(response.content)
    with redis_client() as redis:
        redis.set(key, response.content, ex=3600)


class CertificatePending(Exception):
    pass


def verify(message):
    fields = ['Message', 'MessageId', 'Timestamp', 'TopicArn', 'Type']
    kind = message.get('Type')
    if kind == 'SubscriptionConfirmation':
        fields += ['SubscribeURL', 'Token']
    elif kind != 'Notification':
        raise ValueError('Unsupported SNS notification')
    if 'Subject' in message and kind == 'Notification':
        fields += ['Subject']
    if any(not isinstance(message.get(key), str) or len(message[key]) > 262144 for key in fields):
        raise ValueError('Invalid SNS notification fields')
    key = certificate_key(message.get('SigningCertURL', ''))
    signature = base64.b64decode(message.get('Signature', ''), validate=True)
    version = message.get('SignatureVersion')
    if version not in ('1', '2') or len(signature) not in (256, 512):
        raise ValueError('Invalid SNS signature')
    with redis_client() as redis:
        pem = redis.get(key)
    if not pem:
        raise CertificatePending()
    canonical = ''.join(f'{field}\n{message[field]}\n' for field in sorted(fields)).encode()
    load_certificate(pem).verify(signature, canonical, padding.PKCS1v15(),
                                  hashes.SHA256() if version == '2' else hashes.SHA1())


def process(message):
    kind = message['Type']
    payload = json.loads(message['Message']) if kind == 'Notification' else {}
    event = payload.get('notificationType', payload.get('eventType', 'ignored'))
    reason = {'Bounce': 'bounce', 'Complaint': 'complaint'}.get(event)
    field = 'bouncedRecipients' if reason == 'bounce' else 'complainedRecipients'
    recipients = payload.get(reason, {}).get(field, []) if reason else []
    if reason and (not isinstance(recipients, list) or not 1 <= len(recipients) <= 50):
        raise ValueError('Invalid feedback recipients')
    digests = {address_hash(recipient['emailAddress']) for recipient in recipients}
    with Session.begin() as db:
        accepted = db.execute(insert(EmailFeedbackEvent).values(id=message['MessageId'],
            kind='confirmation' if kind == 'SubscriptionConfirmation' else reason or 'ignored')
            .on_conflict_do_nothing(index_elements=['id']).returning(EmailFeedbackEvent.id)).scalar_one_or_none()
        if accepted is None:
            return
        if kind == 'SubscriptionConfirmation':
            identity = uid()
            data = json.dumps({'action': 'confirm', 'token': message['Token']}).encode()
            db.add(EmailOutbox(id=identity, recipient_hash=hashlib.sha256(message['TopicArn'].encode()).hexdigest(),
                               payload_enc=encrypt_system('email', identity, data),
                               expires_at=now() + timedelta(days=1)))
        for digest in digests:
            db.execute(insert(EmailSuppression).values(recipient_hash=digest, reason=reason)
                       .on_conflict_do_update(index_elements=['recipient_hash'], set_={
                           'reason': case((EmailSuppression.reason == 'complaint', 'complaint'), else_=reason)}))
            db.execute(update(EmailOutbox).where(EmailOutbox.recipient_hash == digest,
                                                 EmailOutbox.status == 'queued')
                       .values(status='suppressed', payload_enc=None))
