"""Send generic Inbox alerts from worker jobs, retaining delivery state in PostgreSQL."""
import json
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from pywebpush import WebPushException, webpush
from requests.exceptions import RequestException
from sqlalchemy import and_, select

from ..config import settings
from ..db import Conversation, InboxPushSubscription, Membership, Session, now

log = logging.getLogger(__name__)
PUSH_HOSTS = {'fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com'}


def supported_endpoint(endpoint: str) -> bool:
    try:
        url = urlsplit(endpoint)
        return (url.scheme == 'https' and url.hostname in PUSH_HOSTS and url.port in (None, 443)
                and not url.username and not url.password and not url.fragment)
    except ValueError:
        return False


def configured() -> bool:
    return bool(settings.inbox_vapid_public_key and settings.inbox_vapid_private_key
                and settings.inbox_vapid_subject)


def deliver_waiting() -> None:
    if not configured():
        return
    # Rotate through small batches so a slow push provider cannot monopolise the worker.
    with Session.begin() as db:
        subscriptions = db.scalars(
            select(InboxPushSubscription)
            .join(Membership, and_(Membership.tenant_id == InboxPushSubscription.tenant_id,
                                   Membership.user_id == InboxPushSubscription.user_id))
            .where(InboxPushSubscription.next_attempt_at <= now(),
                   Membership.role.in_(('owner', 'admin', 'agent')))
            .order_by(InboxPushSubscription.next_attempt_at, InboxPushSubscription.endpoint_hash)
            .limit(5).with_for_update(skip_locked=True, of=InboxPushSubscription)
        ).all()
        for subscription in subscriptions:
            subscription.next_attempt_at = now() + timedelta(minutes=2)

    for subscription in subscriptions:
        identity = (subscription.tenant_id, subscription.user_id, subscription.endpoint_hash)
        with Session() as db:
            conversations = db.scalars(select(Conversation).where(
                Conversation.tenant_id == subscription.tenant_id, Conversation.status == 'waiting',
            )).all()
            pending = {row.id: (row.last_message_at or row.created_at).isoformat() for row in conversations}
        changed = any(subscription.notification_state.get(key) != value for key, value in pending.items())
        expired = False
        succeeded = not changed
        if changed and supported_endpoint(subscription.endpoint):
            try:
                webpush(
                    subscription_info={'endpoint': subscription.endpoint,
                                       'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth}},
                    data=json.dumps({'workspace_id': subscription.tenant_id}),
                    vapid_private_key=settings.inbox_vapid_private_key,
                    vapid_claims={'sub': settings.inbox_vapid_subject}, ttl=300, timeout=5,
                )
                succeeded = True
            except WebPushException as exc:
                expired = exc.response is not None and exc.response.status_code in (404, 410)
                log.warning('Inbox push provider rejected delivery')
            except (RequestException, ValueError):
                log.warning('Inbox push delivery unavailable')
        with Session.begin() as db:
            current = db.get(InboxPushSubscription, identity, with_for_update=True)
            if current is None or current.auth != subscription.auth or current.p256dh != subscription.p256dh:
                continue
            if expired:
                db.delete(current)
            else:
                # Successful markers survive Redis restarts. A crashed worker's lease expires.
                if succeeded:
                    current.notification_state = pending
                current.next_attempt_at = now() + timedelta(minutes=1)
