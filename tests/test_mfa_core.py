"""RFC vectors, encrypted setup, one-use proofs and durable challenge attempt limits."""

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import select

from app.config import settings
from app.db import Membership, Session, User, UserSession, now, uid
from app.models.mfa import MFAChallenge, UserMFA
from app.services import auth, crypto, mfa

PASSWORD = "authenticator-test-passphrase"


@pytest.fixture
def account(tenant, monkeypatch):
    monkeypatch.setattr(settings, "master_key", base64.b64encode(b"m" * 32).decode())
    with Session.begin() as db:
        user = User(
            id=uid(),
            email="mfa@example.com",
            name="Account",
            password_hash=auth.hash_password(PASSWORD),
            email_verified_at=now(),
        )
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant["tenant_id"], role="owner"))
        raw, csrf = auth.issue_session(db, user.id)
        identity = user.id
    return identity, auth.hash_token(raw), csrf


def action(account, operation, *args):
    error, result = None, None
    with Session.begin() as db:
        user = db.get(User, account[0], with_for_update=True)
        current = db.get(UserSession, account[1])
        try:
            result = operation(db, user, current, *args)
        except mfa.MFAError as exc:
            error = exc
    if error:
        raise error
    return result


def activate(account):
    setup = action(account, mfa.enroll, PASSWORD)
    secret = base64.b32decode(setup["secret"])
    result, tokens = action(account, mfa.enable, mfa.step(secret))
    return secret, result["recovery_codes"], (account[0], auth.hash_token(tokens[0]), tokens[1])


def test_rfc6238_six_digit_vectors_and_replay_window(monkeypatch):
    monkeypatch.setattr(settings, "master_key", base64.b64encode(b"m" * 32).decode())
    secret = b"12345678901234567890"
    assert [mfa.step(secret, t) for t in (59, 1111111109, 1111111111, 1234567890, 2000000000)] == [
        "287082",
        "081804",
        "050471",
        "005924",
        "279037",
    ]
    moment = 1234567890
    monkeypatch.setattr(mfa, "now", lambda: datetime.fromtimestamp(moment, timezone.utc))
    assert mfa.verify_totp(secret, mfa.step(secret, moment - 30)) == moment // 30 - 1
    assert mfa.verify_totp(secret, mfa.step(secret, moment + 30)) == moment // 30 + 1
    assert mfa.verify_totp(secret, mfa.step(secret, moment - 60)) is None
    assert mfa.verify_totp(secret, mfa.step(secret, moment), moment // 30) is None
    with pytest.raises(InvalidTag):
        packet = crypto.encrypt_system("totp", "first-user", secret)
        crypto.decrypt_system("totp", "second-user", packet)


def test_setup_is_session_bound_encrypted_and_activation_rotates_sessions(account):
    setup = action(account, mfa.enroll, PASSWORD)
    secret = base64.b32decode(setup["secret"])
    assert setup["uri"].startswith("otpauth://totp/MarketingAdda%3A") and "period=30" in setup["uri"]
    with Session.begin() as db:
        value = db.get(UserMFA, account[0])
        assert secret not in value.pending_enc and setup["secret"].encode() not in value.pending_enc
        other, csrf = auth.issue_session(db, account[0])
    other_account = account[0], auth.hash_token(other), csrf
    with pytest.raises(mfa.MFAError, match="Setup expired"):
        action(other_account, mfa.enable, mfa.step(secret))
    enrollment_code = mfa.step(secret)
    result, tokens = action(account, mfa.enable, enrollment_code)
    assert len(result["recovery_codes"]) == 10
    with Session() as db:
        value = db.get(UserMFA, account[0])
        assert value.secret_enc and value.pending_enc is None and value.enabled_at
        assert db.get(UserSession, account[1]) is None and db.get(UserSession, other_account[1]) is None
        assert db.get(UserSession, auth.hash_token(tokens[0])).mfa_verified
        assert not mfa.proof(value, enrollment_code)  # The enrollment proof cannot be replayed at login.
        assert not any(code.replace("-", "") in value.recovery_hashes for code in result["recovery_codes"])


def test_recovery_once_and_disable_keeps_current_rotated_session(account):
    _, codes, current = activate(account)
    with Session.begin() as db:
        value = db.get(UserMFA, account[0])
        assert mfa.proof(value, codes[0]) and not mfa.proof(value, codes[0])
    result, tokens = action(current, mfa.manage, PASSWORD, codes[1], "regenerate")
    assert len(result["recovery_codes"]) == 10 and set(result["recovery_codes"]).isdisjoint(codes)
    current = account[0], auth.hash_token(tokens[0]), tokens[1]
    with pytest.raises(mfa.MFAError):
        action(current, mfa.manage, PASSWORD, codes[2], "disable")
    result, tokens = action(current, mfa.manage, PASSWORD, result["recovery_codes"][0], "disable")
    assert result["status"] == "disabled"
    with Session() as db:
        value = db.get(UserMFA, account[0])
        assert not value.enabled_at and not value.secret_enc and not value.recovery_hashes
        assert not db.get(UserSession, auth.hash_token(tokens[0])).mfa_verified


def test_bad_setup_codes_commit_attempts_and_expire_pending_secret(account):
    action(account, mfa.enroll, PASSWORD)
    for _ in range(5):
        with pytest.raises(mfa.MFAError):
            action(account, mfa.enable, "invalid-code")
    with Session() as db:
        assert db.get(UserMFA, account[0]).pending_enc is None
    action(account, mfa.enroll, PASSWORD)
    with Session.begin() as db:
        db.get(UserMFA, account[0]).pending_expires_at = now() - timedelta(seconds=1)
    with pytest.raises(mfa.MFAError, match="Setup expired"):
        action(account, mfa.enable, "000000")


def test_login_challenge_nonce_expiry_password_binding_and_attempts(account):
    _, codes, current = activate(account)
    with Session.begin() as db:
        user = db.get(User, account[0])
        raw, csrf = mfa.challenge(db, user)
    key = auth.hash_token(raw)

    def attempt(nonce, code):
        error = None
        with Session.begin() as db:
            user = db.get(User, account[0], with_for_update=True)
            value = db.get(MFAChallenge, key)
            try:
                mfa.finish(db, user, value, nonce, code)
            except mfa.MFAError as exc:
                error = exc
        if error:
            raise error

    with pytest.raises(mfa.MFAError):
        attempt("wrong-nonce", codes[0])
    with Session() as db:
        assert db.get(MFAChallenge, key).attempts == 0
    for _ in range(5):
        with pytest.raises(mfa.MFAError):
            attempt(csrf, "invalid-code")
    with pytest.raises(mfa.MFAError):
        attempt(csrf, codes[0])
    with Session.begin() as db:
        user = db.get(User, account[0])
        raw, csrf = mfa.challenge(db, user)
        key = auth.hash_token(raw)
        user.password_hash = auth.hash_password("a-different-password")
    with pytest.raises(mfa.MFAError):
        attempt(csrf, codes[0])
    with Session.begin() as db:
        raw, csrf = mfa.challenge(db, db.get(User, account[0]))
        key = auth.hash_token(raw)
        db.get(MFAChallenge, key)  # New challenges are flushed by the next lookup.
        db.flush()
        db.get(MFAChallenge, key).expires_at = now() - timedelta(seconds=1)
    with pytest.raises(mfa.MFAError):
        attempt(csrf, codes[0])


def test_enrollment_requires_verified_privilege_but_demoted_users_can_recover(account):
    _, codes, current = activate(account)
    with Session.begin() as db:
        db.scalar(select(Membership).where(Membership.user_id == account[0])).role = "viewer"
    result, _ = action(current, mfa.manage, PASSWORD, codes[0], "disable")
    assert result["status"] == "disabled"
    with Session() as db:
        session_id = db.scalar(select(UserSession.id_hash).where(UserSession.user_id == account[0]))
    with pytest.raises(mfa.MFAError) as caught:
        action((account[0], session_id, ""), mfa.enroll, PASSWORD)
    assert caught.value.status == 403
