"""TOTP enrollment, one-use recovery codes and atomic account security transitions."""
import base64
import secrets
from datetime import timedelta
from urllib.parse import quote, urlencode

from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.twofactor.totp import TOTP
from sqlalchemy import delete, select

from ..config import settings
from ..db import Membership, Session, Tenant, UserSession, now
from ..models.mfa import MFAChallenge, UserMFA
from . import auth, crypto
from .audit import audit


class MFAError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def eligible(db, user):
    return user.is_platform_admin or bool(db.scalar(select(Membership.id).join(Tenant,
        Tenant.id == Membership.tenant_id).where(Membership.user_id == user.id,
        Membership.role.in_(['owner', 'admin']), Tenant.suspended.is_(False)).limit(1)))


def log(db, user_id, action):
    for tenant_id in db.scalars(select(Membership.tenant_id).where(Membership.user_id == user_id)):
        audit(db, tenant_id, action, actor_user_id=user_id)


def state(db, user_id):
    value = db.get(UserMFA, user_id)
    if value is None:
        value = UserMFA(user_id=user_id)
        db.add(value)
        db.flush()
    return value


def status(user, session_hash):
    with Session() as db:
        value = db.get(UserMFA, user.id)
        return dict(enabled=bool(value and value.enabled_at), eligible=eligible(db, user),
            email_verified=user.email_verified_at is not None,
            configured=bool(settings.master_key), recovery_remaining=len(value.recovery_hashes) if value else 0,
            setup_pending=bool(value and value.pending_session == session_hash and value.pending_expires_at
                               and value.pending_expires_at > now()))


def step(secret, timestamp=None):
    return TOTP(secret, 6, SHA1(), 30).generate(now().timestamp() if timestamp is None else timestamp).decode()


def verify_totp(secret, proof, last_step=-1):
    current = int(now().timestamp()) // 30
    for counter in (current + 1, current, current - 1):
        if counter > last_step and secrets.compare_digest(step(secret, counter * 30), proof):
            return counter
    return None


def clear_pending(value):
    value.pending_enc = value.pending_expires_at = value.pending_session = value.pending_password = None
    value.pending_attempts = 0


def recovery_codes(value):
    raw = [secrets.token_hex(16) for _ in range(10)]
    value.recovery_hashes = [auth.hash_token(value.user_id + ':' + code) for code in raw]
    return ['-'.join(code[i:i+8] for i in range(0, 32, 8)) for code in raw]


def proof(value, code):
    if not value.enabled_at or not value.secret_enc:
        return False
    secret = crypto.decrypt_system('totp', value.user_id, value.secret_enc)
    if (counter := verify_totp(secret, code, value.last_step)) is not None:
        value.last_step = counter
        return True
    normalized = code.replace('-', '').replace(' ', '').lower()
    hashed = auth.hash_token(value.user_id + ':' + normalized)
    match = next((h for h in value.recovery_hashes if secrets.compare_digest(h, hashed)), None)
    if match:
        value.recovery_hashes = [h for h in value.recovery_hashes if h != match]
        return True
    return False


def rotate(db, user, current_session, *, verified):
    ip, agent = current_session.ip, current_session.user_agent
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.execute(delete(MFAChallenge).where(MFAChallenge.user_id == user.id))
    return auth.issue_session(db, user.id, ip, agent, mfa_verified=verified)


def enroll(db, user, current_session, password):
    if not eligible(db, user) or user.email_verified_at is None:
        raise MFAError('A verified owner or administrator account is required', 403)
    if not auth.verify_password(user.password_hash, password):
        raise MFAError('Invalid password', 401)
    value = state(db, user.id)
    if value.enabled_at:
        raise MFAError('Two-factor authentication is already enabled', 409)
    secret = secrets.token_bytes(20)
    value.pending_enc = crypto.encrypt_system('totp', user.id, secret)
    value.pending_session, value.pending_password = current_session.id_hash, auth.hash_token(user.password_hash)
    value.pending_expires_at, value.pending_attempts = now() + timedelta(minutes=10), 0
    key = base64.b32encode(secret).decode()
    uri = 'otpauth://totp/' + quote(settings.totp_issuer + ':' + user.email, safe='') + '?' + urlencode({
        'secret': key, 'issuer': settings.totp_issuer, 'algorithm': 'SHA1', 'digits': 6, 'period': 30})
    log(db, user.id, 'auth.mfa_setup_started')
    return dict(secret=key, uri=uri, expires_at=value.pending_expires_at)


def enable(db, user, current_session, code):
    value = state(db, user.id)
    if (value.enabled_at or not value.pending_enc or not value.pending_expires_at
            or value.pending_expires_at <= now() or value.pending_attempts >= 5
            or value.pending_session != current_session.id_hash
            or value.pending_password != auth.hash_token(user.password_hash)):
        raise MFAError('Setup expired or changed; start again', 409)
    if not eligible(db, user) or user.email_verified_at is None:
        raise MFAError('A verified owner or administrator account is required', 403)
    secret = crypto.decrypt_system('totp', user.id, value.pending_enc)
    counter = verify_totp(secret, code)
    if counter is None:
        value.pending_attempts += 1
        if value.pending_attempts >= 5:
            clear_pending(value)
        raise MFAError('Invalid authenticator code', 401)
    value.secret_enc, value.enabled_at, value.last_step = value.pending_enc, now(), counter
    value.version += 1
    clear_pending(value)
    codes = recovery_codes(value)
    tokens = rotate(db, user, current_session, verified=True)
    log(db, user.id, 'auth.mfa_enabled')
    return dict(recovery_codes=codes), tokens


def manage(db, user, current_session, password, code, action):
    if not auth.verify_password(user.password_hash, password):
        raise MFAError('Invalid password', 401)
    value = state(db, user.id)
    if not proof(value, code):
        raise MFAError('Invalid authenticator or recovery code', 401)
    value.version += 1
    if action == 'disable':
        value.secret_enc, value.enabled_at, value.last_step, value.recovery_hashes = None, None, -1, []
        clear_pending(value)
        result = {'status': 'disabled'}
    else:
        result = dict(recovery_codes=recovery_codes(value))
    tokens = rotate(db, user, current_session, verified=action != 'disable')
    log(db, user.id, 'auth.mfa_' + action)
    return result, tokens


def challenge(db, user):
    value = state(db, user.id)
    raw, csrf = auth.new_token(), auth.new_token()
    db.execute(delete(MFAChallenge).where(MFAChallenge.user_id == user.id))
    db.add(MFAChallenge(id_hash=auth.hash_token(raw), user_id=user.id, csrf_hash=auth.hash_token(csrf),
        password_digest=auth.hash_token(user.password_hash), version=value.version,
        expires_at=now() + timedelta(minutes=5)))
    return raw, csrf


def finish(db, user, value, csrf, code):
    mfa = state(db, user.id)
    if (not value or value.expires_at <= now() or value.attempts >= 5 or not mfa.enabled_at
            or value.version != mfa.version or value.password_digest != auth.hash_token(user.password_hash)
            or not secrets.compare_digest(value.csrf_hash, auth.hash_token(csrf))):
        raise MFAError('Invalid or expired sign-in challenge', 401)
    value.attempts += 1
    if not proof(mfa, code):
        log(db, user.id, 'auth.mfa_failed')
        raise MFAError('Invalid authenticator or recovery code', 401)
    db.delete(value)
    log(db, user.id, 'auth.mfa_succeeded')
