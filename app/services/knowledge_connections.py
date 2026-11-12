"""Configuration and session-bound Drive consent. Requests never contact providers."""
import base64
import hashlib
import json
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from sqlalchemy import delete, select

from ..config import settings
from ..db import Membership, Session, Source, Tenant, UserSession, now, uid
from ..integrations.knowledge_sync.http import HTTP, SyncError
from ..models.knowledge_sync import KnowledgeConnection as Connection
from ..spreadsheets.store import remove
from . import crypto
from .audit import audit

SCOPE = 'https://www.googleapis.com/auth/drive.readonly'


def configured():
    url = urlsplit(settings.knowledge_google_redirect_uri)
    secure = url.scheme == 'https' or settings.environment != 'production' and url.scheme == 'http' and (
        url.hostname in ('localhost', '127.0.0.1'))
    return bool(settings.master_key and settings.knowledge_google_client_id and settings.knowledge_google_client_secret
                and secure and url.hostname and not (url.username or url.password or url.query or url.fragment))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def owned(db, tenant_id, identity, lock=False):
    q = select(Connection).where(Connection.tenant_id == tenant_id, Connection.id == identity)
    row = db.scalar(q.with_for_update() if lock else q)
    if not row:
        raise SyncError('not_found', status=404)
    return row


def view(row):
    return {k: getattr(row, k) for k in ('id', 'provider', 'label', 'selection', 'enabled', 'status',
                                       'last_success', 'last_error', 'next_run', 'lease')}


def listing(tenant_id):
    with Session() as db:
        connections = [view(c) for c in db.scalars(select(Connection).where(Connection.tenant_id == tenant_id))]
        sources = [{k: getattr(s, k) for k in ('id', 'filename', 'status', 'sync_connection_id')}
                   for s in db.scalars(select(Source).where(Source.tenant_id == tenant_id,
                                                            Source.sync_connection_id.is_not(None)))]
    return {'drive_configured': configured(), 'connections': connections, 'sources': sources}


def create(tenant_id, provider, label, selection, token=None, *, user_id=None, session_hash=None):
    if provider == 'drive' and not configured():
        raise SyncError('oauth_not_configured')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    packet = crypto.encrypt(tenant_id, json.dumps({'verifier': verifier} if provider == 'drive' else {'token': token}))
    with Session.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if db.scalar(select(Connection.id).where(Connection.tenant_id == tenant_id, Connection.provider == provider)):
            raise SyncError('disconnect_existing_connection')
        row = Connection(id=uid(), tenant_id=tenant_id, provider=provider, label=label, selection=selection)
        if provider == 'drive':
            row.status, row.oauth_state_hash, row.oauth_secret_enc = 'requested', digest(state), packet
            row.oauth_user_id, row.oauth_session_hash = user_id, session_hash
            row.oauth_expires_at = now() + timedelta(minutes=10)
        else:
            row.credentials_enc, row.next_run = packet, now()
        db.add(row)
        db.flush()
        audit(db, tenant_id, 'knowledge_sync.connected', row.id, actor_type='admin')
    if provider == 'notion':
        return view(row)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return {'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': settings.knowledge_google_client_id, 'redirect_uri': settings.knowledge_google_redirect_uri,
        'scope': SCOPE, 'state': state, 'response_type': 'code', 'access_type': 'offline',
        'prompt': 'consent select_account', 'code_challenge': challenge, 'code_challenge_method': 'S256'})}


def callback(state, code, session_hash, user_id, denied=False):
    with Session() as db:
        row = db.scalar(select(Connection).where(Connection.oauth_state_hash == digest(state)))
        if not row:
            raise SyncError('oauth_state_invalid', status=404)
        tenant_id, identity = row.tenant_id, row.id
        private = json.loads(crypto.decrypt(tenant_id, row.oauth_secret_enc))
    packet = crypto.encrypt(tenant_id, json.dumps(private | {'code': code}))
    with Session.begin() as db:
        row = owned(db, tenant_id, identity, True)
        member = db.scalar(select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == user_id))
        if (row.oauth_state_hash != digest(state) or row.oauth_expires_at <= now() or
                row.oauth_session_hash != session_hash or row.oauth_user_id != user_id or
                not member or member.role not in ('owner', 'admin') or db.get(Tenant, tenant_id).suspended):
            raise SyncError('oauth_session_changed', status=403)
        row.oauth_state_hash, row.oauth_secret_enc = None, None if denied else packet
        row.status, row.next_run = ('needs_attention', None) if denied else ('connecting', now())


def purge(db, row):
    for source in db.scalars(select(Source).where(Source.tenant_id == row.tenant_id,
                                                 Source.sync_connection_id == row.id)):
        remove(db, row.tenant_id, source.id)
    db.execute(delete(Source).where(Source.tenant_id == row.tenant_id, Source.sync_connection_id == row.id))


def change(tenant_id, identity, *, selection=None, enabled=None, disconnect=False):
    with Session.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        row = owned(db, tenant_id, identity, True)
        if disconnect:
            purge(db, row)
            db.delete(row)  # Local disconnect destroys credentials; remote token revocation is user-managed.
            audit(db, tenant_id, 'knowledge_sync.disconnected', identity, actor_type='admin')
            return
        if selection is not None:
            purge(db, row)
            row.selection, row.cursor, row.last_success = selection, None, None
        if enabled is not None:
            row.enabled, row.last_success = enabled, None
        row.lease, row.next_run, row.last_error = None, now(), None
        audit(db, tenant_id, 'knowledge_sync.updated', identity, actor_type='admin')
        return view(row)


def access(row):
    private = json.loads(crypto.decrypt(row.tenant_id, row.credentials_enc or row.oauth_secret_enc))
    if row.provider == 'notion':
        return private['token']
    if row.credentials_enc and private['expires_at'] > now().timestamp() + 120:
        return private['access_token']
    if row.credentials_enc:
        form = {'grant_type': 'refresh_token', 'refresh_token': private['refresh_token']}
    else:
        with Session() as db:
            user_session = db.get(UserSession, row.oauth_session_hash)
            member = db.scalar(select(Membership).where(Membership.tenant_id == row.tenant_id,
                                                        Membership.user_id == row.oauth_user_id))
            if (not user_session or user_session.expires_at <= now() or not member or
                    member.role not in ('owner', 'admin') or row.oauth_expires_at <= now()):
                raise SyncError('authorization_required')
        form = {'grant_type': 'authorization_code', 'code': private['code'], 'code_verifier': private['verifier'],
                'redirect_uri': settings.knowledge_google_redirect_uri}
    data = HTTP().request('oauth', 'POST', 'token', form=form | {
        'client_id': settings.knowledge_google_client_id, 'client_secret': settings.knowledge_google_client_secret})
    refresh = data.get('refresh_token', private.get('refresh_token'))
    scopes = data.get('scope', SCOPE if row.credentials_enc else '').split()
    if (not isinstance(data.get('access_token'), str) or not refresh or scopes != [SCOPE]
            or not 300 <= int(data.get('expires_in', 0)) <= 86400):
        raise SyncError('authorization_required')
    packet = crypto.encrypt(row.tenant_id, json.dumps({'access_token': data['access_token'], 'refresh_token': refresh,
                            'expires_at': now().timestamp() + int(data['expires_in'])}))
    with Session.begin() as db:
        current = owned(db, row.tenant_id, row.id, True)
        if current.lease != row.lease or not current.enabled:
            raise SyncError('stale_run')
        current.credentials_enc, current.oauth_secret_enc, current.status = packet, None, 'connected'
    return data['access_token']
