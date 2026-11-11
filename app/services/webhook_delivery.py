"""Leased, fenced, at-least-once delivery with a 24-hour retry window."""
import asyncio
import secrets
from datetime import timedelta

from redis.exceptions import RedisError
from sqlalchemy import and_, delete, or_, select, update

from ..config import settings
from ..db import Membership, Session, Tenant, User, now
from ..integrations.outgoing_webhooks import DeliveryError, Transport, body_bytes, signature
from ..models.webhooks import WebhookDelivery as Delivery
from ..models.webhooks import WebhookEndpoint as Endpoint
from ..rate_limits import RateLimiter
from . import crypto, email


def scoped(db, model, tenant_id, identity, lock=False, skip=False):
    query = select(model).where(model.tenant_id == tenant_id, model.id == identity)
    return db.scalar(query.with_for_update(skip_locked=skip) if lock else query)


def claim():
    with Session.begin() as db:
        candidates = db.execute(select(Delivery.id, Delivery.tenant_id, Delivery.endpoint_id).where(
            Delivery.next_attempt_at <= now(), Delivery.expires_at > now(), or_(Delivery.state == 'queued',
                and_(Delivery.state == 'sending', Delivery.claimed_at < now() - timedelta(minutes=2))))
            .order_by(Delivery.next_attempt_at, Delivery.id).limit(30)).all()
        for identity, tenant_id, endpoint_id in candidates:
            # Match configuration/resend lock order: endpoint, then delivery.
            endpoint = scoped(db, Endpoint, tenant_id, endpoint_id, True, True)
            if not endpoint:
                continue
            row = scoped(db, Delivery, tenant_id, identity, True, True)
            if (not row or row.expires_at <= now() or row.state not in ('queued', 'sending')
                    or row.next_attempt_at > now() or row.state == 'sending'
                    and row.claimed_at >= now() - timedelta(minutes=2)):
                continue
            if not endpoint.enabled or db.get(Tenant, tenant_id).suspended:
                row.state, row.last_error = 'cancelled', 'destination_unavailable'
                continue
            row.state, row.claimed_at, row.attempts = 'sending', now(), row.attempts + 1
            return dict(id=identity, tenant_id=tenant_id, endpoint_id=endpoint_id, stamp=row.claimed_at,
                        attempt=row.attempts, url=endpoint.url, packet=endpoint.secret_enc, payload=row.payload)
    return None


async def process_one(transport=None):
    job = await asyncio.to_thread(claim)
    if not job:
        return False
    stamp, code, error, retryable, delay, quota_wait = job['stamp'], None, None, True, 0, False
    try:
        limiter = RateLimiter(settings.outgoing_webhook_requests_per_minute, namespace='outgoing-webhooks')
        if not await asyncio.to_thread(limiter.allow, job['tenant_id']):
            error, quota_wait, delay = 'workspace_rate_limit', True, 60
        else:
            secret = await asyncio.to_thread(crypto.decrypt, job['tenant_id'], job['packet'])
            raw, timestamp = body_bytes(job['payload']), int(now().timestamp())
            headers = {'Content-Type': 'application/json', 'X-Signature': signature(secret.decode(), timestamp, raw),
                       'X-Event-Id': job['payload']['id'], 'User-Agent': 'MarketingAddaWebhooks/1.0'}
            code, delay = await (transport or Transport()).send(job['url'], raw, headers)
            if not 200 <= code < 300:
                error = 'redirect_rejected' if 300 <= code < 400 else 'receiver_rejected'
                retryable = code in (408, 429) or code >= 500
    except RedisError:
        error, quota_wait, delay = 'quota_unavailable', True, 60
    except DeliveryError as exc:
        error, retryable = exc.code, exc.retryable
    except Exception:
        error = 'delivery_unavailable'  # Never expose URL, plaintext secret, payload or exception text.
    with Session.begin() as db:
        endpoint = scoped(db, Endpoint, job['tenant_id'], job['endpoint_id'], True)
        row = scoped(db, Delivery, job['tenant_id'], job['id'], True)
        if not endpoint or not row or row.state != 'sending' or row.claimed_at != stamp:
            return True
        if quota_wait:
            row.state, row.last_error, row.attempts = 'queued', error, row.attempts - 1
            row.next_attempt_at = now() + timedelta(seconds=delay)
            return True
        row.response_code, row.last_error = code, error
        row.history = [*row.history[-63:], {'at': now().isoformat(), 'code': code, 'error': error}]
        if error:
            endpoint.failure_since = endpoint.failure_since or now()
            endpoint.last_error = error
            base = (5, 30, 120, 600, 1800, 3600)[min(job['attempt'] - 1, 5)]
            seconds = max(delay, base + secrets.randbelow(max(1, base // 10)))
            row.next_attempt_at = min(row.expires_at, now() + timedelta(seconds=seconds))
            row.state = 'queued' if retryable and row.expires_at > now() else 'failed'
        else:
            row.state = 'delivered'
            endpoint.last_success_at, endpoint.last_error, endpoint.failure_since = now(), None, None
    return True


def maintain():
    with Session.begin() as db:
        for endpoint in db.scalars(select(Endpoint).where(Endpoint.enabled,
                Endpoint.failure_since <= now() - timedelta(days=3)).with_for_update(skip_locked=True)):
            endpoint.enabled, endpoint.disabled_reason = False, 'continuous_failure'
            endpoint.notification_pending = True
            db.execute(update(Delivery).where(Delivery.tenant_id == endpoint.tenant_id,
                Delivery.endpoint_id == endpoint.id, Delivery.state.in_(['queued', 'sending']))
                .values(state='failed', last_error='endpoint_disabled'))
        db.execute(update(Delivery).where(Delivery.state.in_(['queued', 'sending']), Delivery.expires_at <= now())
                   .values(state='failed', last_error='retry_window_expired'))
        db.execute(delete(Delivery).where(Delivery.created_at < now() - timedelta(days=30)))


def notify_one():
    with Session.begin() as db:
        endpoint = db.scalar(select(Endpoint).where(Endpoint.notification_pending,
            Endpoint.disabled_reason == 'continuous_failure').with_for_update(skip_locked=True).limit(1))
        if not endpoint:
            return False
        # Queue email in this transaction. The sender never makes network calls here.
        recipients = db.scalars(select(User.email).join(Membership, Membership.user_id == User.id).where(
            Membership.tenant_id == endpoint.tenant_id, Membership.role.in_(['owner', 'admin']))).all()
        for recipient in recipients:
            email.sender.stage(db, recipient, 'Webhook destination disabled',
                               'A destination has had no successful delivery for three days. Review Developer API.')
        endpoint.notification_pending = False
    return True
