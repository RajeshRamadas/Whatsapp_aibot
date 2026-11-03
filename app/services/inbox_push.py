"""Deliver generic Inbox alerts without exposing conversation content."""
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from pywebpush import WebPushException, webpush
from redis import Redis
from sqlalchemy import and_, select

from ..config import settings
from ..db import Conversation, InboxPushSubscription, Membership, Session, now

log = logging.getLogger(__name__)
PUSH_HOSTS = {'fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com'}


def supported_endpoint(endpoint: str) -> bool:
    url = urlsplit(endpoint)
    return (url.scheme == 'https' and url.hostname in PUSH_HOSTS and url.port in (None, 443)
            and not url.username and not url.password and not url.fragment)


def deliver_waiting() -> None:
    if not (settings.inbox_vapid_private_key and settings.inbox_vapid_subject):
        return
    with Session() as db:
        rows = db.execute(
            select(InboxPushSubscription, Conversation.id, Conversation.last_message_at)
            .join(Conversation, Conversation.tenant_id == InboxPushSubscription.tenant_id)
            .join(Membership, and_(Membership.tenant_id == InboxPushSubscription.tenant_id,
                                   Membership.user_id == InboxPushSubscription.user_id))
            .where(Conversation.status == 'waiting', Conversation.last_message_at >= now() - timedelta(minutes=5),
                   Membership.role.in_(('owner', 'admin', 'agent')))
            .order_by(Conversation.last_message_at.desc())
        ).all()
    with Redis.from_url(settings.redis_url, socket_timeout=2) as redis:
        for subscription, conversation_id, timestamp in rows:
            if not supported_endpoint(subscription.endpoint):
                continue
            key = (f'inbox-push:{subscription.tenant_id}:{subscription.user_id}:'
                   f'{subscription.endpoint_hash}:{conversation_id}:{timestamp.isoformat()}')
            if not redis.set(key, '1', nx=True, ex=600):
                continue
            try:
                webpush(
                    subscription_info={'endpoint': subscription.endpoint,
                                       'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth}},
                    data='{}', vapid_private_key=settings.inbox_vapid_private_key,
                    vapid_claims={'sub': settings.inbox_vapid_subject}, ttl=60, timeout=5,
                )
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    with Session.begin() as db:
                        stale = db.get(InboxPushSubscription, (
                            subscription.tenant_id, subscription.user_id, subscription.endpoint_hash,
                        ))
                        if stale and stale.auth == subscription.auth and stale.p256dh == subscription.p256dh:
                            db.delete(stale)
                else:
                    redis.delete(key)
                    log.warning('Inbox push delivery failed')
