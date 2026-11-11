"""Google drive.file consent for lead exports, bound to the initiating administrator session."""
import base64
import hashlib
import json
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from sqlalchemy import select

from ..config import settings
from ..db import Membership, Session, Tenant, UserSession, now
from ..integrations.lead_sync.http import HTTP, SyncError
from ..models.lead_sync import LeadConnection as Connection
from . import crypto
from .appointments import AppointmentError, row
from .audit import audit
from .lead_sync import queue

SCOPE = 'https://www.googleapis.com/auth/drive.file'
TOKEN = 'https://oauth2.googleapis.com/'


def configured():
    url = urlsplit(settings.lead_sync_google_redirect_uri or '')
    secure = url.scheme == 'https' or settings.environment != 'production' and url.scheme == 'http' and (
        url.hostname in ('localhost', '127.0.0.1'))
    return bool(settings.master_key and settings.lead_sync_google_client_id and settings.lead_sync_google_client_secret
                and secure and url.hostname and not (url.username or url.password or url.query or url.fragment))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def start(tenant_id, user_id, session_hash, label):
    if not configured():
        raise AppointmentError('Google lead-export OAuth is not configured')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    packet = crypto.encrypt(tenant_id, json.dumps({'verifier': verifier}))
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        for old in db.scalars(select(Connection).where(Connection.tenant_id == tenant_id,
                Connection.provider == 'sheets', Connection.status == 'requested',
                Connection.oauth_expires_at < now())):
            old.status, old.oauth_state_hash, old.oauth_secret_enc = 'disconnected', None, None
        if db.scalar(select(Connection.id).where(Connection.tenant_id == tenant_id, Connection.provider == 'sheets',
                                                  Connection.status != 'disconnected').limit(1)):
            raise AppointmentError('Disconnect the current Google lead-export account first')
        c = Connection(tenant_id=tenant_id, provider='sheets', label=label, status='requested',
            oauth_state_hash=digest(state), oauth_secret_enc=packet, oauth_user_id=user_id,
            oauth_session_hash=session_hash, oauth_expires_at=now() + timedelta(minutes=10))
        db.add(c)
        db.flush()
        audit(db, tenant_id, 'lead_sync.oauth_requested', c.id, actor_type='user', actor_user_id=user_id)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return {'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': settings.lead_sync_google_client_id, 'redirect_uri': settings.lead_sync_google_redirect_uri,
        'scope': SCOPE, 'state': state, 'response_type': 'code', 'access_type': 'offline',
        'prompt': 'consent select_account', 'code_challenge': challenge, 'code_challenge_method': 'S256'})}


def callback(state, code, session_hash, user_id, denied=False):
    # OAuth state is the pre-tenant capability. All subsequent records use its resolved tenant.
    with Session() as db:
        c = db.scalar(select(Connection).where(Connection.oauth_state_hash == digest(state)))
        if not c:
            raise AppointmentError('Invalid or consumed OAuth state', 404)
        tenant_id, identity, packet = c.tenant_id, c.id, c.oauth_secret_enc
    verifier = json.loads(crypto.decrypt(tenant_id, packet))['verifier']
    encrypted = crypto.encrypt(tenant_id, json.dumps({'code': code, 'verifier': verifier}))
    with Session.begin() as db:
        c = row(db, Connection, tenant_id, identity, lock=True)
        membership = db.scalar(select(Membership).where(Membership.tenant_id == tenant_id,
            Membership.user_id == user_id))
        if (c.oauth_state_hash != digest(state) or c.oauth_expires_at <= now() or c.oauth_user_id != user_id
                or c.oauth_session_hash != session_hash or not membership or membership.role not in ('owner', 'admin')
                or db.get(Tenant, tenant_id).suspended):
            raise AppointmentError('OAuth session or administrator permission changed', 403)
        c.oauth_state_hash, c.status = None, 'disconnected' if denied else 'connecting'
        c.oauth_secret_enc = None if denied else encrypted
        if not denied:
            queue(db, c, 'oauth')


def tokens(data, previous=None):
    if (not isinstance(data, dict) or not isinstance(data.get('access_token'), str)
            or data.get('error') or data.get('scope') and SCOPE not in data['scope'].split()):
        raise SyncError('credentials_required')
    refresh = data.get('refresh_token') or (previous or {}).get('refresh_token')
    try:
        seconds = int(data.get('expires_in', 3600))
    except (ValueError, TypeError):
        raise SyncError('invalid_response') from None
    if not isinstance(refresh, str) or not 300 <= seconds <= 86400:
        raise SyncError('credentials_required')
    return {'access_token': data['access_token'], 'refresh_token': refresh,
            'expires_at': (now() + timedelta(seconds=seconds)).isoformat()}


def exchange(tenant_id, identity, revision):
    with Session() as db:
        c = row(db, Connection, tenant_id, identity)
        session = db.get(UserSession, c.oauth_session_hash)
        membership = db.scalar(select(Membership).where(Membership.tenant_id == tenant_id,
                                                         Membership.user_id == c.oauth_user_id))
        if (not session or session.expires_at <= now() or not membership
                or membership.role not in ('owner', 'admin') or c.revision != revision):
            raise SyncError('credentials_required')
        packet = c.oauth_secret_enc
    secret = json.loads(crypto.decrypt(tenant_id, packet))
    value = HTTP(tenant_id, 'sheets', TOKEN).request('POST', 'token', data={
        'client_id': settings.lead_sync_google_client_id, 'client_secret': settings.lead_sync_google_client_secret,
        'redirect_uri': settings.lead_sync_google_redirect_uri, 'grant_type': 'authorization_code',
        'code': secret['code'], 'code_verifier': secret['verifier']})
    encrypted = crypto.encrypt(tenant_id, json.dumps(tokens(value)))
    with Session.begin() as db:
        c = row(db, Connection, tenant_id, identity, lock=True)
        if c.revision != revision or c.status == 'disconnected':
            raise SyncError('connection_changed')
        c.credentials_enc, c.oauth_secret_enc, c.status = encrypted, None, 'connected'


def access(tenant_id, identity, revision):
    from datetime import datetime
    with Session() as db:
        c = row(db, Connection, tenant_id, identity)
        if c.revision != revision or c.status == 'disconnected' or not c.credentials_enc:
            raise SyncError('connection_changed')
        packet = c.credentials_enc
    value = json.loads(crypto.decrypt(tenant_id, packet))
    if datetime.fromisoformat(value['expires_at']) <= now() + timedelta(minutes=5):
        fresh = HTTP(tenant_id, 'sheets', TOKEN).request('POST', 'token', data={
            'client_id': settings.lead_sync_google_client_id, 'client_secret': settings.lead_sync_google_client_secret,
            'grant_type': 'refresh_token', 'refresh_token': value['refresh_token']})
        value = tokens(fresh, value)
        encrypted = crypto.encrypt(tenant_id, json.dumps(value))
        with Session.begin() as db:
            c = row(db, Connection, tenant_id, identity, lock=True)
            if c.revision != revision or c.status == 'disconnected':
                raise SyncError('connection_changed')
            c.credentials_enc = encrypted
    return value['access_token']
