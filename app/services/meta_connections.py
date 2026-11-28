"""Shared verified Meta enrollment; provider calls never retain SQL sessions."""
import hashlib
from dataclasses import dataclass

from pydantic import SecretStr
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..config import settings
from ..db import Channel, Session, Tenant
from .crypto import encrypt
from .meta_signup import MetaSignup, MetaSignupError


class ConnectionError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ConnectionRequest:
    kind: str
    external_id: str
    access_token: SecretStr


def debug_token(graph, token):
    app_token = f'{settings.whatsapp_app_id}|{settings.whatsapp_app_secret}'
    debug = graph.request('debug_token', params={'input_token': token}, token=app_token).get('data', {})
    if not isinstance(debug, dict):
        raise MetaSignupError('Meta did not return token information')
    return debug


def verify_connection(graph, kind: str, external_id: str, token: str) -> str | None:
    """Verify the supplied token can access the claimed Meta asset before linking it."""
    debug = debug_token(graph, token)
    if debug.get('is_valid') is not True or str(debug.get('app_id')) != settings.whatsapp_app_id:
        raise MetaSignupError('The token does not belong to the configured Meta app')
    marker = 'ig_id' if kind == 'instagram' else 'category'
    result = graph.request(external_id, params={'fields': 'id,name,' + marker}, token=token)
    if str(result.get('id')) != external_id:
        raise MetaSignupError('Meta did not verify ownership of the selected asset')
    value = result.get(marker)
    valid = (str(value).isdigit() and int(value) > 0 and not isinstance(value, bool)) if kind == 'instagram' else (
        isinstance(value, str) and bool(value.strip()))
    if not valid:
        raise ConnectionError(422, 'The account does not match the selected messaging channel')
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
        raise ConnectionError(424,
                              'Meta subscription cleanup needs administrator verification before retrying') from None


def credential_digest(channel):
    return hashlib.sha256(channel.credentials_enc or b'').hexdigest()


def renewal_channel(db, body, tenant_id, renewal, *, lock=False):
    stmt = select(Channel).where(Channel.id == renewal['channel_id'], Channel.tenant_id == tenant_id,
        Channel.kind == body.kind, Channel.external_id == body.external_id)
    channel = db.scalar(stmt.with_for_update() if lock else stmt)
    if not channel or credential_digest(channel) != renewal['credential_digest']:
        raise ConnectionError(409, 'The messaging account changed; start authorization again')
    return channel


def persist_connection(body, tenant_id, name, encrypted, guard=None, finalize=None, renewal=None):
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        if guard:
            guard(db)
        if renewal:
            channel = renewal_channel(db, body, tenant_id, renewal, lock=True)
            channel.credentials_enc = encrypted
            channel.meta_token_status, channel.meta_token_check_at = None, None
            channel.meta_token_expires_at, channel.meta_token_alerted = None, False
            if name:
                channel.display_name = name
        else:
            if db.scalar(select(Channel.id).where(Channel.tenant_id == tenant_id, Channel.kind == body.kind)):
                raise ConnectionError(409, 'A channel of this kind is already connected')
            channel = Channel(tenant_id=tenant_id, kind=body.kind, external_id=body.external_id,
                              display_name=name, credentials_enc=encrypted)
            db.add(channel)
            db.flush()
        if finalize:
            finalize(db, channel)
        return channel


def connect(body, tenant_id, *, graph=None, guard=None, finalize=None, renewal=None):
    with Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2) as redis:
        lease = redis.lock('locks:meta-dm-connection:' + body.external_id, timeout=300, blocking=False)
        try:
            acquired = lease.acquire(blocking=False)
        except RedisError:
            raise ConnectionError(503, 'Connection coordination is unavailable') from None
        if not acquired:
            raise ConnectionError(409, 'This Meta account is already being connected')
        try:
            return connect_asset(body, tenant_id, lease, graph, guard, finalize, renewal)
        finally:
            try:
                lease.release()
            except RedisError:
                pass


def connect_asset(body, tenant_id, lease, graph=None, guard=None, finalize=None, renewal=None):
    if not settings.whatsapp_app_id or not settings.whatsapp_app_secret or not settings.whatsapp_verify_token:
        raise ConnectionError(503, 'Meta webhook verification is not configured')
    token = body.access_token.get_secret_value()
    if not 1 <= len(token) <= 8192 or any(char.isspace() for char in token):
        raise ConnectionError(422, 'Invalid Meta access token')
    with Session() as db:
        if guard:
            guard(db)
        if renewal:
            renewal_channel(db, body, tenant_id, renewal)
        else:
            existing = db.scalar(select(Channel.id).where(Channel.kind == body.kind,
                                                        Channel.external_id == body.external_id))
            if existing is not None:
                raise ConnectionError(409, 'This Meta asset is already connected')
            if db.scalar(select(Channel.id).where(Channel.tenant_id == tenant_id, Channel.kind == body.kind)):
                raise ConnectionError(409, 'Disconnect the current channel before connecting another')
    # Check local encryption before any provider-side change.
    try:
        encrypted = encrypt(tenant_id, token)
    except ValueError:
        raise ConnectionError(503, 'Credential encryption is unavailable') from None
    graph, added = graph or MetaSignup(), False
    try:
        name = verify_connection(graph, body.kind, body.external_id, token)
        added = not already_subscribed(graph, body.external_id, token)
        fields = 'messages,messaging_seen' if body.kind == 'instagram' else 'messages,message_deliveries,message_reads'
        if graph.request(f'{body.external_id}/subscribed_apps', params={'subscribed_fields': fields},
                         token=token, payload={}).get('success') is not True:
            raise MetaSignupError('Meta did not confirm messaging webhook subscription')
        return persist_connection(body, tenant_id, name, encrypted, guard, finalize, renewal)
    except MetaSignupError:
        undo_subscription(graph, body, token, added, lease)
        raise ConnectionError(502, 'Meta could not verify and subscribe this asset') from None
    except IntegrityError:
        undo_subscription(graph, body, token, added, lease)
        raise ConnectionError(409, 'This Meta asset is already connected') from None
    except (ConnectionError, SQLAlchemyError) as exc:
        undo_subscription(graph, body, token, added, lease)
        if isinstance(exc, ConnectionError):
            raise
        raise ConnectionError(503, 'The connection could not be saved') from None
