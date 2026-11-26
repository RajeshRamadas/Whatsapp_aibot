"""Session-bound Meta consent, encrypted discovery and durable account selection."""
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta

from pydantic import SecretStr
from sqlalchemy import delete, func, or_, select, true, update

from ..config import settings
from ..db import Membership, Session, Tenant, UserSession, now, uid
from ..integrations import meta_oauth as provider
from ..models.meta_oauth import MetaAuthorization as Attempt
from . import crypto, meta_connections
from .audit import audit
from .meta_connections import ConnectionError
from .meta_signup import MetaSignup

TERMINAL = ('complete', 'denied', 'cancelled', 'failed', 'expired')
WORKING = ('discovering', 'connecting')


@dataclass(frozen=True)
class Actor:
    tenant_id: str
    user_id: str
    session_hash: str


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def authorize(db, row):
    session = db.get(UserSession, row.session_hash)
    member = db.scalar(select(Membership).where(Membership.tenant_id == row.tenant_id,
                                                Membership.user_id == row.user_id))
    tenant = db.get(Tenant, row.tenant_id)
    if (not session or session.user_id != row.user_id or session.expires_at <= now() or not member
            or member.role not in ('owner', 'admin') or not tenant or tenant.suspended
            or row.app_id != settings.whatsapp_app_id):
        raise ConnectionError(403, 'Authorization or administrator permission changed')


def owned(db, identity, actor, *, lock=False):
    query = select(Attempt).where(Attempt.id == identity, Attempt.tenant_id == actor.tenant_id,
                                  Attempt.user_id == actor.user_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ConnectionError(404, 'Authorization not found')
    if row.session_hash != actor.session_hash:
        raise ConnectionError(403, 'Continue in the browser session that started authorization')
    authorize(db, row)
    return row


def finish(row, status, reason=None):
    row.status, row.last_error = status, reason
    row.secret_enc, row.state_hash, row.claim_token = None, None, None


def view(row):
    assets = json.loads(crypto.decrypt(row.tenant_id, row.secret_enc))['accounts'] if (
        row.status == 'selecting' and row.secret_enc) else []
    return {'id': row.id, 'kind': row.kind, 'status': row.status, 'channel_id': row.channel_id,
            'error': row.last_error, 'accounts': [{key: account[key] for key in ('external_id', 'name', 'kind')}
                                                for account in assets]}


def expire(tenant_id=None):
    scope = Attempt.tenant_id == tenant_id if tenant_id else true()
    with Session.begin() as db:
        db.execute(update(Attempt).where(scope, Attempt.expires_at <= now(), or_(Attempt.secret_enc.is_not(None),
            Attempt.state_hash.is_not(None))).values(status='expired', last_error='authorization_expired',
                secret_enc=None, state_hash=None, claim_token=None))
        db.execute(update(Attempt).where(scope, Attempt.status.in_(WORKING),
            Attempt.claimed_at < now() - timedelta(minutes=5)).values(status='failed', last_error='interrupted',
                secret_enc=None, state_hash=None, claim_token=None))
        db.execute(delete(Attempt).where(scope, Attempt.expires_at < now() - timedelta(days=1)))


def start(actor, kind):
    if not provider.configured():
        raise ConnectionError(503, 'Meta authorization is not configured')
    state = secrets.token_urlsafe(32)
    try:
        packet = crypto.encrypt(actor.tenant_id, json.dumps({'redirect_uri': settings.meta_dm_oauth_redirect_uri}))
    except ValueError:
        raise ConnectionError(503, 'Credential encryption is unavailable') from None
    expire(actor.tenant_id)
    with Session.begin() as db:
        db.get(Tenant, actor.tenant_id, with_for_update=True)
        count = db.scalar(select(func.count()).select_from(Attempt).where(Attempt.tenant_id == actor.tenant_id,
            Attempt.user_id == actor.user_id, Attempt.status.not_in(TERMINAL), Attempt.expires_at > now()))
        if count >= 3:
            raise ConnectionError(409, 'Finish or cancel an existing authorization first')
        row = Attempt(tenant_id=actor.tenant_id, user_id=actor.user_id, session_hash=actor.session_hash,
            app_id=settings.whatsapp_app_id, kind=kind, state_hash=digest(state),
            expires_at=now() + timedelta(minutes=10), secret_enc=packet)
        authorize(db, row)
        db.add(row)
        db.flush()
        audit(db, row.tenant_id, 'meta.authorization_started', row.id, actor_type='user', actor_user_id=row.user_id)
        identity = row.id
    return {'id': identity, 'url': provider.authorization_url(kind, state)}


def callback(state, code, user_id, session_hash, workspace=None, denied=False):
    # The random state is the sole pre-tenant capability; user/session checks follow.
    with Session.begin() as db:
        row = db.scalar(select(Attempt).where(Attempt.state_hash == digest(state)).with_for_update())
        if row is None:
            raise ConnectionError(404, 'Invalid or consumed authorization state')
        if row.user_id != user_id or row.session_hash != session_hash or (workspace and workspace != row.tenant_id):
            raise ConnectionError(403, 'Authorization session or workspace changed')
        authorize(db, row)
        if row.status != 'authorizing' or row.expires_at <= now():
            raise ConnectionError(403, 'Authorization expired')
        if denied:
            finish(row, 'denied', 'authorization_denied')
        else:
            packet = json.loads(crypto.decrypt(row.tenant_id, row.secret_enc))
            row.secret_enc = crypto.encrypt(row.tenant_id, json.dumps({**packet, 'code': code}))
            row.state_hash, row.status = None, 'queued'
        return row.id


def status(actor, identity):
    expire(actor.tenant_id)
    with Session() as db:
        return view(owned(db, identity, actor))


def select_account(actor, identity, external_id):
    with Session.begin() as db:
        row = owned(db, identity, actor, lock=True)
        if row.expires_at <= now() or row.status != 'selecting':
            raise ConnectionError(409, 'Authorization is not ready for account selection')
        packet = json.loads(crypto.decrypt(row.tenant_id, row.secret_enc))
        if external_id not in {account['external_id'] for account in packet['accounts']}:
            raise ConnectionError(422, 'Choose an account returned by this authorization')
        row.selected_id, row.status = external_id, 'connect_queued'
        return view(row)


def cancel(actor, identity):
    with Session.begin() as db:
        row = owned(db, identity, actor, lock=True)
        if row.status == 'complete':
            raise ConnectionError(409, 'This account is connected; disconnect it from Messaging accounts')
        finish(row, 'cancelled')
        return view(row)


def process_one():
    expire()
    with Session.begin() as db:
        row = db.scalar(select(Attempt).where(Attempt.status.in_(('queued', 'connect_queued')),
            Attempt.expires_at > now()).order_by(Attempt.created_at, Attempt.id)
            .with_for_update(skip_locked=True).limit(1))
        if row is None:
            return False
        operation = 'discovering' if row.status == 'queued' else 'connecting'
        row.status, row.claim_token, row.claimed_at = operation, uid(), now()
        identity, tenant_id, claim, packet, kind = row.id, row.tenant_id, row.claim_token, row.secret_enc, row.kind
    error = None
    result = None
    try:
        if not provider.configured():
            raise ConnectionError(503, 'Meta authorization is not configured')
        with Session() as db:
            current = db.scalar(select(Attempt).where(Attempt.id == identity, Attempt.tenant_id == tenant_id))
            if current is None:
                return True
            if current.status != operation or current.claim_token != claim or current.expires_at <= now():
                return True
            authorize(db, current)
        secret = json.loads(crypto.decrypt(tenant_id, packet))
        if operation == 'discovering':
            accounts = provider.discover(MetaSignup(), secret['code'], secret['redirect_uri'], kind)
            if not accounts:
                error = 'no_accounts'
            else:
                result = crypto.encrypt(tenant_id, json.dumps({'accounts': accounts}))
        else:
            def guard(db):
                current = db.scalar(select(Attempt).where(Attempt.id == identity, Attempt.tenant_id == tenant_id)
                                    .with_for_update())
                if (not current or current.status != operation or current.claim_token != claim
                        or current.expires_at <= now()):
                    raise ConnectionError(403, 'Authorization was cancelled or expired')
                authorize(db, current)
            def complete(db, channel):
                current = db.get(Attempt, identity)
                current.channel_id = channel.id
                finish(current, 'complete')
                audit(db, tenant_id, 'meta.authorization_connected', channel.id, actor_type='user',
                      actor_user_id=current.user_id)
            selected = next(account for account in secret['accounts'] if account['external_id'] == row.selected_id)
            meta_connections.connect(meta_connections.ConnectionRequest(kind, selected['external_id'],
                SecretStr(selected['token'])), tenant_id, graph=MetaSignup(), guard=guard, finalize=complete)
    except ConnectionError as exc:
        error = {403: 'authorization_changed', 409: 'connection_conflict', 424: 'cleanup_required',
                 503: 'setup_unavailable'}.get(exc.status, 'provider_failed')
    except Exception:
        error = 'provider_failed'  # Never store provider error bodies or credential values.
    with Session.begin() as db:
        current = db.scalar(select(Attempt).where(Attempt.id == identity, Attempt.tenant_id == tenant_id)
                            .with_for_update())
        if not current or current.claim_token != claim or current.status != operation:
            return True
        try:
            authorize(db, current)
        except ConnectionError:
            error = 'authorization_changed'
        if current.expires_at <= now():
            error = 'authorization_expired'
        if error:
            finish(current, 'failed', error)
        elif operation == 'discovering':
            current.status, current.secret_enc, current.claim_token = 'selecting', result, None
        else:
            finish(current, 'failed', 'interrupted')
    return True
