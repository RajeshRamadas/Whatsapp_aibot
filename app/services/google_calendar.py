"""Tenant-scoped Google OAuth. Handlers only persist work; workers own network I/O."""
import hashlib
import json
import secrets
from datetime import timedelta

from sqlalchemy import func, select

from ..db import Membership, Session, Tenant, User, UserSession, now
from ..integrations.google_calendar import SCOPES, CalendarError, Client, authorization_url, configured
from ..models.google_calendar import GoogleConnection as Connection
from ..models.google_calendar import GoogleTask as Task
from . import crypto, email
from .appointments import AppointmentError, row
from .audit import audit


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def view(value):
    return {key: getattr(value, key) for key in ('id', 'label', 'status', 'calendars', 'last_success_at', 'last_error')}


def listing(tenant_id):
    with Session() as db:
        return {'configured': configured(), 'connections': [view(c) for c in db.scalars(select(Connection).where(
            Connection.tenant_id == tenant_id).order_by(Connection.created_at))]}


def enqueue(db, connection, kind):
    db.add(Task(tenant_id=connection.tenant_id, connection_id=connection.id, kind=kind, revision=connection.revision))


def start(tenant_id, user_id, session_hash, label):
    if not configured():
        raise AppointmentError('Google OAuth is not configured', 409)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    packet = crypto.encrypt(tenant_id, json.dumps({'verifier': verifier}))
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        # Expired abandoned consent attempts do not exhaust the account limit.
        for old in db.scalars(select(Connection).where(Connection.tenant_id == tenant_id,
                Connection.status == 'requested', Connection.oauth_expires_at <= now())):
            old.status, old.oauth_state_hash, old.oauth_secret_enc = 'disconnected', None, None
        count = db.scalar(select(func.count()).select_from(Connection).where(
            Connection.tenant_id == tenant_id, Connection.status != 'disconnected'))
        if count >= 5:
            raise AppointmentError('At most five Google accounts can be connected')
        connection = Connection(tenant_id=tenant_id, label=label, oauth_state_hash=digest(state),
            oauth_session_hash=session_hash, oauth_user_id=user_id, oauth_expires_at=now() + timedelta(minutes=10),
            oauth_secret_enc=packet)
        db.add(connection)
        db.flush()
        audit(db, tenant_id, 'google_calendar.connect_requested', connection.id,
            actor_user_id=user_id, actor_type='user')
    return {'url': authorization_url(state, verifier)}


def callback(state, code, session_hash, user_id, denied=False):
    # OAuth state is the pre-tenant capability; all subsequent access uses its tenant.
    with Session() as db:
        candidate = db.scalar(select(Connection).where(Connection.oauth_state_hash == digest(state)))
        if not candidate:
            raise AppointmentError('Invalid or consumed OAuth state', 404)
        tenant_id, identity, packet = candidate.tenant_id, candidate.id, candidate.oauth_secret_enc
    verifier = json.loads(crypto.decrypt(tenant_id, packet))['verifier']
    encrypted = crypto.encrypt(tenant_id, json.dumps({'verifier': verifier, 'code': code}))
    with Session.begin() as db:
        connection = row(db, Connection, tenant_id, identity, lock=True)
        membership = db.scalar(select(Membership).where(Membership.tenant_id == tenant_id,
            Membership.user_id == user_id))
        if (connection.oauth_state_hash != digest(state) or connection.oauth_expires_at <= now()
                or connection.oauth_session_hash != session_hash or connection.oauth_user_id != user_id
                or not membership or membership.role not in ('owner', 'admin') or db.get(Tenant, tenant_id).suspended):
            raise AppointmentError('OAuth session or administrator permission changed', 403)
        connection.oauth_state_hash = None
        connection.oauth_secret_enc = None if denied else encrypted
        connection.status = 'disconnected' if denied else 'connecting'
        if not denied:
            enqueue(db, connection, 'connect')


def change(tenant_id, identity, disconnect=False):
    with Session.begin() as db:
        connection = row(db, Connection, tenant_id, identity, lock=True)
        if disconnect:
            connection.revision += 1
            connection.status, connection.oauth_state_hash, connection.oauth_secret_enc = 'disconnecting', None, None
        elif connection.status not in ('connected', 'needs_attention') or not connection.credentials_enc:
            raise AppointmentError('Connect this account first')
        enqueue(db, connection, 'disconnect' if disconnect else 'refresh')
        audit(db, tenant_id, 'google_calendar.disconnect' if disconnect else 'google_calendar.refresh', identity)
        return view(connection)


def access(tenant_id, connection_id, revision=None):
    """Caller owns the connection lease. Refresh credentials before expiry, outside DB sessions."""
    with Session() as db:
        c = row(db, Connection, tenant_id, connection_id)
        if not c.credentials_enc or c.status not in ('connected', 'needs_attention') or (
                revision is not None and c.revision != revision):
            raise CalendarError('authorization_required')
        packet, expires, version = c.credentials_enc, c.token_expires_at, c.revision
    tokens = json.loads(crypto.decrypt(tenant_id, packet))
    if not expires or expires <= now() + timedelta(minutes=5):
        try:
            fresh = Client(tenant_id).refresh(tokens['refresh_token'])
        except CalendarError as exc:
            if not exc.retry:
                with Session.begin() as db:
                    c = row(db, Connection, tenant_id, connection_id, lock=True)
                    callbacks = attention(db, c, 'authorization_required') if c.revision == version else []
                for callback in callbacks:
                    callback()
            raise
        tokens = validate_tokens(fresh, tokens)
        packet = crypto.encrypt(tenant_id, json.dumps(tokens))
        with Session.begin() as db:
            c = row(db, Connection, tenant_id, connection_id, lock=True)
            if c.revision != version or c.status == 'disconnecting':
                raise CalendarError('authorization_required')
            c.credentials_enc, c.token_expires_at = packet, now() + timedelta(seconds=tokens['expires_in'])
    return tokens['access_token']


def validate_tokens(data, previous=None):
    if not isinstance(data, dict) or not isinstance(data.get('access_token'), str):
        raise CalendarError('invalid_response')
    if data.get('scope') and not set(SCOPES).issubset(data['scope'].split()):
        raise CalendarError('missing_scope')
    refresh = data.get('refresh_token') or (previous or {}).get('refresh_token')
    if not isinstance(refresh, str) or not refresh:
        raise CalendarError('authorization_required')
    try:
        expiry = int(data.get('expires_in', 3600))
    except (ValueError, TypeError):
        raise CalendarError('invalid_response') from None
    if not 300 <= expiry <= 86400:
        raise CalendarError('invalid_response')
    return {'access_token': data['access_token'], 'refresh_token': refresh, 'expires_in': expiry}


def execute(tenant_id, identity, kind, revision):
    with Session() as db:
        c = row(db, Connection, tenant_id, identity)
        if c.revision != revision:
            return {}
        packet, actor, session_hash = c.oauth_secret_enc, c.oauth_user_id, c.oauth_session_hash
        credentials = c.credentials_enc
        if kind == 'connect':
            session = db.get(UserSession, session_hash)
            membership = db.scalar(select(Membership).where(Membership.tenant_id == tenant_id,
                Membership.user_id == actor))
            if (not session or session.expires_at <= now() or not membership
                    or membership.role not in ('owner', 'admin')):
                raise CalendarError('authorization_required')
    if kind == 'availability':
        from .calendar_availability import fetch
        return fetch(tenant_id, identity, revision)
    provider = Client(tenant_id)
    if kind == 'disconnect':
        if credentials:
            provider.disconnect(json.loads(crypto.decrypt(tenant_id, credentials))['refresh_token'])
        return {'credentials_enc': None, 'oauth_secret_enc': None, 'calendars': [], 'status': 'disconnected'}
    if kind == 'connect':
        secret = json.loads(crypto.decrypt(tenant_id, packet))
        tokens = validate_tokens(provider.connect(secret['code'], secret['verifier']))
        encrypted = crypto.encrypt(tenant_id, json.dumps(tokens))
        # Persist tokens before catalog lookup: a transient list failure must not replay a one-use code.
        with Session.begin() as db:
            c = row(db, Connection, tenant_id, identity, lock=True)
            if c.revision != revision or c.status == 'disconnecting':
                return {}
            c.credentials_enc, c.token_expires_at = encrypted, now() + timedelta(seconds=tokens['expires_in'])
            c.oauth_secret_enc, c.status = None, 'connected'
        token = tokens['access_token']
    else:
        token = access(tenant_id, identity, revision)
    return {'calendars': provider.test(token), 'status': 'connected'}


def attention(db, connection, code):
    callbacks = []
    if connection.status != 'needs_attention':
        for user in db.scalars(select(User).join(Membership, Membership.user_id == User.id).where(
                Membership.tenant_id == connection.tenant_id, Membership.role.in_(['owner', 'admin']),
                User.email_verified_at.is_not(None))):
            callbacks.append(email.sender.stage(db, user.email, 'Google Calendar needs attention',
                'Reconnect the Google account from Appointments. Calendar-linked slots are paused until it recovers.'))
    connection.status, connection.last_error = 'needs_attention', code
    return callbacks
