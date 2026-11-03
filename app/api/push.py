"""Manage a signed-in agent's Inbox notification subscriptions."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from ..config import settings
from ..services.inbox_push import configured, supported_endpoint
from ..db import InboxPushSubscription, Session
from .principal import Principal, require_role
from .shared import sha

router = APIRouter()


class SubscriptionEndpoint(BaseModel):
    endpoint: HttpUrl

    @field_validator('endpoint')
    @classmethod
    def validate_endpoint(cls, value: HttpUrl) -> HttpUrl:
        if not supported_endpoint(str(value)):
            raise ValueError('Unsupported push provider endpoint')
        return value


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(pattern=r'^[A-Za-z0-9_-]{87}$')
    auth: str = Field(pattern=r'^[A-Za-z0-9_-]{22}$')


class Subscription(SubscriptionEndpoint):
    keys: SubscriptionKeys


@router.put('/api/v1/inbox/push-subscription', status_code=204)
def subscribe(body: Subscription, principal: Principal = Depends(require_role('agent'))):
    if principal.user is None:
        raise HTTPException(403, 'User session required')
    if not configured():
        raise HTTPException(503, 'Push notifications are not configured')
    endpoint = str(body.endpoint)
    with Session.begin() as db:
        statement = insert(InboxPushSubscription).values(
            tenant_id=principal.tenant.id, user_id=principal.user.id,
            endpoint_hash=sha(endpoint), endpoint=endpoint, **body.keys.model_dump(),
        )
        db.execute(statement.on_conflict_do_update(
            index_elements=['tenant_id', 'user_id', 'endpoint_hash'],
            set_={'p256dh': statement.excluded.p256dh, 'auth': statement.excluded.auth},
        ))


@router.delete('/api/v1/inbox/push-subscription', status_code=204)
def unsubscribe(body: SubscriptionEndpoint, principal: Principal = Depends(require_role('agent'))):
    if principal.user is None:
        raise HTTPException(403, 'User session required')
    with Session.begin() as db:
        db.execute(delete(InboxPushSubscription).where(
            InboxPushSubscription.tenant_id == principal.tenant.id,
            InboxPushSubscription.user_id == principal.user.id,
            InboxPushSubscription.endpoint_hash == sha(str(body.endpoint)),
        ))


@router.get('/api/v1/inbox/push-config')
def push_config(principal: Principal = Depends(require_role('agent'))):
    if principal.user is None:
        raise HTTPException(403, 'User session required')
    return {'public_key': settings.inbox_vapid_public_key if configured() else None}


@router.get('/api/v1/inbox/push-subscription')
def subscription_status(endpoint: str = Query(min_length=1, max_length=4096),
                        principal: Principal = Depends(require_role('agent'))):
    if principal.user is None:
        raise HTTPException(403, 'User session required')
    with Session() as db:
        row = db.get(InboxPushSubscription, (principal.tenant.id, principal.user.id, sha(endpoint)))
    return {'enabled': row is not None}
