"""Admin-only verified Meta Page/Instagram connection lifecycle."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, SecretStr, StringConstraints
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..config import settings
from ..db import Channel, Conversation, Message, MetaDmEvent, Session, Tenant
from ..services.crypto import encrypt
from ..services.events import publish_sync
from ..services.meta_signup import MetaSignup, MetaSignupError
from .channels import view
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/meta-dm')
GraphId = Annotated[str, StringConstraints(pattern=r'^[0-9]{1,32}$')]


class Connect(BaseModel):
    kind: Annotated[str, StringConstraints(pattern=r'^(instagram|messenger)$')]
    external_id: GraphId
    access_token: SecretStr


def admin(principal: Principal = Depends(require_role('admin'))):
    if principal.user is None:
        raise HTTPException(403, 'A signed-in workspace admin is required')
    return principal


def verify_connection(graph, kind: str, external_id: str, token: str) -> str | None:
    """Verify the supplied token can access the claimed Meta asset before linking it."""
    app_token = f'{settings.whatsapp_app_id}|{settings.whatsapp_app_secret}'
    debug = graph.request('debug_token', params={'input_token': token}, token=app_token).get('data', {})
    if (not isinstance(debug, dict) or debug.get('is_valid') is not True
            or str(debug.get('app_id')) != settings.whatsapp_app_id):
        raise MetaSignupError('The token does not belong to the configured Meta app')
    marker = 'ig_id' if kind == 'instagram' else 'category'
    result = graph.request(external_id, params={'fields': 'id,name,' + marker}, token=token)
    if str(result.get('id')) != external_id:
        raise MetaSignupError('Meta did not verify ownership of the selected asset')
    value = result.get(marker)
    valid = (str(value).isdigit() and int(value) > 0 and not isinstance(value, bool)) if kind == 'instagram' else (
        isinstance(value, str) and bool(value.strip()))
    if not valid:
        raise HTTPException(422, 'The account does not match the selected messaging channel')
    name = str(result.get('name') or '')[:200] or None
    return name


def already_subscribed(graph, external_id, token):
    params = {'fields': 'id', 'limit': 100}
    for _ in range(10):
        result = graph.request(f'{external_id}/subscribed_apps', params=params, token=token)
        rows = result.get('data')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise MetaSignupError('Meta did not return subscription state')
        if any(str(row.get('id')) == settings.whatsapp_app_id for row in rows):
            return True
        paging = result.get('paging') or {}
        if not paging.get('next'):
            return False
        after = (paging.get('cursors') or {}).get('after')
        if not after:
            break
        params['after'] = after
    raise MetaSignupError('Meta subscription state could not be verified')


def undo_subscription(graph, body, token, added, lease):
    if not added:
        return
    try:
        with Session() as db:
            # Another completed connection owns this subscription; preserve it.
            if db.scalar(select(Channel.id).where(Channel.kind == body.kind, Channel.external_id == body.external_id)):
                return
        # Never unsubscribe after another enrollment has acquired this asset.
        lease.extend(300, replace_ttl=True)
        result = graph.request(f'{body.external_id}/subscribed_apps', token=token, method='DELETE')
        if result.get('success') is not True:
            raise MetaSignupError('Meta did not confirm cleanup')
    except (MetaSignupError, SQLAlchemyError, RedisError):
        raise HTTPException(424, 'Meta subscription cleanup needs administrator verification before retrying') from None


def persist_connection(body, tenant_id, name, encrypted):
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        if db.scalar(select(Channel.id).where(Channel.tenant_id == tenant_id, Channel.kind == body.kind)):
            raise HTTPException(409, 'A channel of this kind is already connected')
        channel = Channel(tenant_id=tenant_id, kind=body.kind, external_id=body.external_id,
                          display_name=name, credentials_enc=encrypted)
        db.add(channel)
        db.flush()
        return view(channel)


@router.post('/channels', status_code=201)
def connect(body: Connect, principal: Principal = Depends(admin)):
    with Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2) as redis:
        lease = redis.lock('locks:meta-dm-connection:' + body.external_id, timeout=300, blocking=False)
        try:
            acquired = lease.acquire(blocking=False)
        except RedisError:
            raise HTTPException(503, 'Connection coordination is unavailable') from None
        if not acquired:
            raise HTTPException(409, 'This Meta account is already being connected')
        try:
            return connect_asset(body, principal, lease)
        finally:
            try:
                lease.release()
            except RedisError:
                pass


def connect_asset(body, principal, lease):
    if not settings.whatsapp_app_id or not settings.whatsapp_app_secret or not settings.whatsapp_verify_token:
        raise HTTPException(503, 'Meta webhook verification is not configured')
    token = body.access_token.get_secret_value()
    if not 1 <= len(token) <= 8192 or any(char.isspace() for char in token):
        raise HTTPException(422, 'Invalid Meta access token')
    with Session() as db:
        existing = db.scalar(select(Channel).where(Channel.kind == body.kind,
                                                   Channel.external_id == body.external_id))
        if existing is not None:
            raise HTTPException(409, 'This Meta asset is already connected')
        if db.scalar(select(Channel.id).where(Channel.tenant_id == principal.tenant.id,
                                              Channel.kind == body.kind)):
            raise HTTPException(409, 'Disconnect the current channel before connecting another')
    # Check local encryption before any provider-side change.
    try:
        encrypted = encrypt(principal.tenant.id, token)
    except ValueError:
        raise HTTPException(503, 'Credential encryption is unavailable') from None
    graph, added = MetaSignup(), False
    try:
        name = verify_connection(graph, body.kind, body.external_id, token)
        added = not already_subscribed(graph, body.external_id, token)
        if graph.request(f'{body.external_id}/subscribed_apps', params={'subscribed_fields': 'messages'},
                         token=token, payload={}).get('success') is not True:
            raise MetaSignupError('Meta did not confirm messaging webhook subscription')
        return persist_connection(body, principal.tenant.id, name, encrypted)
    except MetaSignupError:
        undo_subscription(graph, body, token, added, lease)
        raise HTTPException(502, 'Meta could not verify and subscribe this asset') from None
    except IntegrityError:
        undo_subscription(graph, body, token, added, lease)
        raise HTTPException(409, 'This Meta asset is already connected') from None
    except (HTTPException, SQLAlchemyError) as exc:
        undo_subscription(graph, body, token, added, lease)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(503, 'The connection could not be saved') from None


@router.delete('/channels/{channel_id}', status_code=204)
def disconnect(channel_id: str, principal: Principal = Depends(admin)):
    with Session.begin() as db:
        db.get(Tenant, principal.tenant.id, with_for_update=True)
        channel = db.scalar(select(Channel).where(Channel.id == channel_id,
                                                   Channel.tenant_id == principal.tenant.id,
                                                   Channel.kind.in_(('instagram', 'messenger'))))
        if channel is None:
            raise HTTPException(404, 'Meta channel not found')
        conversations = db.scalars(select(Conversation.id).where(
            Conversation.tenant_id == principal.tenant.id, Conversation.meta_channel_id == channel.id)).all()
        if db.scalar(select(Message.id).where(Message.conversation_id.in_(conversations),
                                             Message.delivery_status == 'sending').limit(1)):
            raise HTTPException(409, 'A reply is being sent. Wait for it to finish before disconnecting')
        db.execute(update(Message).where(Message.conversation_id.in_(conversations),
            Message.role == 'agent', Message.delivery_status == 'queued').values(
                delivery_status='failed', error='Meta channel disconnected before delivery'))
        db.execute(update(MetaDmEvent).where(MetaDmEvent.tenant_id == principal.tenant.id,
            MetaDmEvent.channel_id == channel.id, MetaDmEvent.status == 'queued').values(status='ignored'))
        db.delete(channel)
    for identity in conversations:
        publish_sync(principal.tenant.id, 'message.updated', {'conversation_id': identity})
        publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': identity})
