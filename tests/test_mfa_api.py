"""Password-only access is insufficient for enrolled accounts; management is always self-scoped."""

import base64
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api import auth
from app.config import settings
from app.db import Membership, Session, User, UserSession, now, uid
from app.models.mfa import MFAChallenge, UserMFA
from app.rate_limits import RateLimiter
from app.services import auth as security
from app.services import mfa
from tests.test_mfa_core import PASSWORD

BASE = "/api/v1/auth/mfa"


@pytest.fixture
def enrolled(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    monkeypatch.setattr(settings, "master_key", base64.b64encode(b"m" * 32).decode())
    limiter = RateLimiter(100, window_seconds=900, namespace="mfa-api-tests")
    limiter.clear()
    monkeypatch.setattr(auth, "_auth_limiter", limiter)
    with Session.begin() as db:
        user = User(
            id=uid(),
            email="secure@example.com",
            name="Security User",
            email_verified_at=now(),
            password_hash=security.hash_password(PASSWORD),
        )
        db.add(user)
        db.flush()
        identity = user.id
        db.add(Membership(user_id=identity, tenant_id=tenant["tenant_id"], role="owner"))
    credentials = {"email": "secure@example.com", "password": PASSWORD}
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 200
    headers = {"X-CSRF-Token": client.cookies[auth.CSRF_COOKIE]}
    setup = client.post(BASE + "/setup", headers=headers, json={"password": PASSWORD})
    assert setup.status_code == 200 and setup.headers["Cache-Control"] == "no-store"
    secret = base64.b32decode(setup.json()["secret"])
    enabled = client.post(BASE + "/enable", headers=headers, json={"code": mfa.step(secret)})
    assert enabled.status_code == 200, enabled.text
    return identity, secret, enabled.json()["recovery_codes"], credentials


def csrf(client):
    return {"X-CSRF-Token": client.cookies[auth.CSRF_COOKIE]}


def test_password_creates_challenge_only_and_recovery_is_single_use(client, enrolled):
    identity, secret, codes, credentials = enrolled
    before = client.cookies[auth.COOKIE]
    challenge = client.post("/api/v1/auth/login", json=credentials)
    assert challenge.status_code == 200 and challenge.json()["mfa_required"] is True
    assert "id" not in challenge.json() and "email" not in challenge.json()
    assert auth.COOKIE not in client.cookies and client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/workspaces").status_code == 401
    with Session() as db:
        assert db.get(UserSession, security.hash_token(before)) is None
        value = db.scalar(select(MFAChallenge).where(MFAChallenge.user_id == identity))
        assert value.id_hash != client.cookies[auth.MFA_COOKIE] and value.csrf_hash != challenge.json()["challenge"]
    # The just-consumed enrollment time step cannot authenticate a new login.
    body = {"challenge": challenge.json()["challenge"], "code": codes[0]}
    assert client.post(BASE + "/verify", json={**body, "challenge": "x" * 43}).status_code == 401
    verified = client.post(BASE + "/verify", json=body)
    assert verified.status_code == 200 and verified.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/auth/me").json()["id"] == identity
    assert auth.MFA_COOKIE not in client.cookies
    assert client.post(BASE + "/verify", json=body).status_code == 401
    next_challenge = client.post("/api/v1/auth/login", json=credentials).json()["challenge"]
    assert client.post(BASE + "/verify", json={**body, "challenge": next_challenge}).status_code == 401
    assert client.post(BASE + "/verify", json={"challenge": next_challenge, "code": codes[1]}).status_code == 200


def test_totp_challenge_expiry_replay_and_non_assured_sessions_fail_closed(client, enrolled, monkeypatch):
    identity, secret, _, credentials = enrolled
    with Session.begin() as db:
        raw, _ = security.issue_session(db, identity)
    cookies = dict(client.cookies)
    client.cookies.clear()
    client.cookies.set(auth.COOKIE, raw)
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()
    client.cookies.update(cookies)
    challenge = client.post("/api/v1/auth/login", json=credentials).json()["challenge"]
    with Session.begin() as db:
        value = db.scalar(select(MFAChallenge).where(MFAChallenge.user_id == identity))
        value.expires_at = now() - timedelta(seconds=1)
    body = {"challenge": challenge, "code": mfa.step(secret)}
    assert client.post(BASE + "/verify", json=body).status_code == 401
    future = now() + timedelta(seconds=30)
    monkeypatch.setattr(mfa, "now", lambda: future)
    challenge = client.post("/api/v1/auth/login", json=credentials).json()["challenge"]
    body = {"challenge": challenge, "code": mfa.step(secret)}
    assert client.post(BASE + "/verify", json=body).status_code == 200
    challenge = client.post("/api/v1/auth/login", json=credentials).json()["challenge"]
    assert client.post(BASE + "/verify", json={**body, "challenge": challenge}).status_code == 401


def test_security_actions_rotate_cookies_revoke_others_and_hide_secrets(client, enrolled):
    identity, _, codes, credentials = enrolled
    with Session.begin() as db:
        other, _ = security.issue_session(db, identity, mfa_verified=True)
    old_cookie, old_csrf = client.cookies[auth.COOKIE], csrf(client)
    assert (
        client.post(
            BASE + "/recovery-codes", headers=old_csrf, json={"password": "wrong", "code": codes[0]}
        ).status_code
        == 401
    )
    regenerated = client.post(BASE + "/recovery-codes", headers=old_csrf, json={"password": PASSWORD, "code": codes[0]})
    assert regenerated.status_code == 200 and client.cookies[auth.COOKIE] != old_cookie
    with Session() as db:
        assert db.get(UserSession, security.hash_token(other)) is None
    status = client.get(BASE)
    assert status.json()["enabled"] and status.json()["recovery_remaining"] == 10
    for code in regenerated.json()["recovery_codes"]:
        assert code not in status.text
    assert "secret" not in status.text and "uri" not in status.text
    assert (
        client.post(BASE + "/disable", headers=old_csrf, json={"password": PASSWORD, "code": codes[1]}).status_code
        == 403
    )
    assert (
        client.post(BASE + "/disable", headers=csrf(client), json={"password": PASSWORD, "code": codes[1]}).status_code
        == 401
    )
    assert (
        client.post(
            BASE + "/disable",
            headers=csrf(client),
            json={"password": PASSWORD, "code": regenerated.json()["recovery_codes"][0]},
        ).status_code
        == 200
    )
    assert not client.get(BASE).json()["enabled"]
    assert "mfa_required" not in client.post("/api/v1/auth/login", json=credentials).json()


def test_account_security_is_not_available_through_api_keys_or_foreign_setup_sessions(client, enrolled, tenant):
    identity, _, codes, _ = enrolled
    assert client.post(BASE + "/setup", headers=csrf(client), json={"password": PASSWORD}).status_code == 409
    assert client.post(BASE + "/disable", json={"password": PASSWORD, "code": codes[0]}).status_code == 403
    for path, body in [
        ("/setup", {"password": ""}),
        ("/enable", {"code": "bad"}),
        ("/disable", {"password": PASSWORD, "code": "bad"}),
        ("/recovery-codes", {"password": PASSWORD}),
        ("/verify", {"challenge": "short", "code": "000000"}),
    ]:
        assert client.post(BASE + path, headers=csrf(client), json=body).status_code == 422
    assert (
        client.post(
            BASE + "/disable", headers=csrf(client), json={"password": PASSWORD, "code": codes[0], "user_id": uid()}
        ).status_code
        == 422
    )
    assert client.delete(BASE + "/setup", headers=csrf(client)).status_code == 204
    client.cookies.clear()
    for method, path, body in [
        ("GET", "", None),
        ("POST", "/setup", {"password": PASSWORD}),
        ("POST", "/enable", {"code": "000000"}),
        ("POST", "/disable", {"password": PASSWORD, "code": codes[0]}),
        ("POST", "/recovery-codes", {"password": PASSWORD, "code": codes[0]}),
        ("DELETE", "/setup", None),
    ]:
        assert client.request(method, BASE + path, headers=tenant["headers"], json=body).status_code == 401


def test_second_factor_attempt_limit_survives_new_challenges(client, enrolled, monkeypatch):
    identity, _, codes, credentials = enrolled
    limiter = RateLimiter(5, window_seconds=900, namespace="mfa-limit-tests")
    limiter.clear()
    monkeypatch.setattr(auth, "_auth_limiter", limiter)
    challenge = client.post("/api/v1/auth/login", json=credentials).json()["challenge"]
    with Session() as db:
        value = db.get(UserMFA, identity)
        secret = mfa.crypto.decrypt_system("totp", identity, value.secret_enc)
        valid = {mfa.step(secret, now().timestamp() + offset) for offset in (-30, 0, 30)}
    invalid = next(f"{number:06d}" for number in range(10) if f"{number:06d}" not in valid)
    for _ in range(5):
        assert client.post(BASE + "/verify", json={"challenge": challenge, "code": invalid}).status_code == 401
    assert client.post(BASE + "/verify", json={"challenge": challenge, "code": codes[0]}).status_code == 429
    replacement = client.post("/api/v1/auth/login", json=credentials).json()["challenge"]
    assert client.post(BASE + "/verify", json={"challenge": replacement, "code": codes[0]}).status_code == 429


def test_password_reset_revokes_challenges_and_still_requires_second_factor(client, enrolled, monkeypatch):
    from app.services import email
    from tests.test_auth import RecordingEmail

    identity, _, codes, credentials = enrolled
    recorder = RecordingEmail()
    monkeypatch.setattr(email, "sender", recorder)
    assert client.post("/api/v1/auth/password-reset/request", json={"email": credentials["email"]}).status_code == 200
    code = recorder.sent[-1][2]
    challenge = client.post("/api/v1/auth/login", json=credentials).json()["challenge"]
    new_password = "a-new-strong-passphrase"
    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"email": credentials["email"], "code": code, "password": new_password},
    )
    assert response.status_code == 200
    assert client.post(BASE + "/verify", json={"challenge": challenge, "code": codes[0]}).status_code == 401
    required = client.post("/api/v1/auth/login", json={**credentials, "password": new_password}).json()
    assert required["mfa_required"] and client.get("/api/v1/auth/me").status_code == 401
    assert client.post(BASE + "/verify", json={"challenge": required["challenge"], "code": codes[0]}).status_code == 200
    assert client.get(BASE).json()["enabled"]


def test_pending_setup_is_private_to_session_and_management_survives_role_demotion(client, enrolled, tenant):
    identity, _, codes, _ = enrolled
    with Session.begin() as db:
        db.scalar(select(Membership).where(Membership.user_id == identity)).role = "viewer"
    assert (
        client.post(BASE + "/disable", headers=csrf(client), json={"password": PASSWORD, "code": codes[0]}).status_code
        == 200
    )
    assert client.post(BASE + "/setup", headers=csrf(client), json={"password": PASSWORD}).status_code == 403
    with Session.begin() as db:
        db.scalar(select(Membership).where(Membership.user_id == identity)).role = "owner"
    setup = client.post(BASE + "/setup", headers=csrf(client), json={"password": PASSWORD}).json()
    original = dict(client.cookies)
    with Session.begin() as db:
        raw, token = security.issue_session(db, identity)
    client.cookies.clear()
    client.cookies.update({auth.COOKIE: raw, auth.CSRF_COOKIE: token})
    assert not client.get(BASE).json()["setup_pending"]
    assert client.delete(BASE + "/setup", headers=csrf(client)).status_code == 404
    assert (
        client.post(
            BASE + "/enable", headers=csrf(client), json={"code": mfa.step(base64.b32decode(setup["secret"]))}
        ).status_code
        == 409
    )
    client.cookies.clear()
    client.cookies.update(original)
    assert client.delete(BASE + "/setup", headers=csrf(client)).status_code == 204
    assert not client.get(BASE).json()["setup_pending"]
    with Session.begin() as db:
        db.get(User, identity).email_verified_at = None
    assert client.post(BASE + "/setup", headers=csrf(client), json={"password": PASSWORD}).status_code == 403


def test_other_account_cannot_read_keys_or_complete_another_login(client, enrolled):
    identity, _, codes, credentials = enrolled
    required = client.post("/api/v1/auth/login", json=credentials).json()
    challenge_cookie = client.cookies[auth.MFA_COOKIE]
    with Session.begin() as db:
        other = User(
            id=uid(),
            email="different@example.com",
            name="Different account",
            email_verified_at=now(),
            password_hash=security.hash_password(PASSWORD),
        )
        db.add(other)
        db.flush()
        raw, token = security.issue_session(db, other.id)
    client.cookies.clear()
    client.cookies.update({auth.COOKIE: raw, auth.CSRF_COOKIE: token})
    assert not client.get(BASE).json()["enabled"] and client.get(BASE).json()["recovery_remaining"] == 0
    assert client.post(BASE + "/verify", json={"challenge": required["challenge"], "code": codes[0]}).status_code == 401
    # An attacker needs the password-verified challenge cookie as well as its body nonce and the proof.
    client.cookies.clear()
    client.cookies.set(auth.MFA_COOKIE, challenge_cookie, path="/api")
    assert client.post(BASE + "/verify", json={"challenge": "x" * 43, "code": codes[0]}).status_code == 401


def test_encryption_outage_fails_closed_without_issuing_login_session(client, enrolled, monkeypatch):
    _, _, codes, credentials = enrolled
    challenge = client.post("/api/v1/auth/login", json=credentials).json()["challenge"]
    monkeypatch.setattr(settings, "master_key", None)
    assert client.post(BASE + "/verify", json={"challenge": challenge, "code": codes[0]}).status_code == 503
    assert client.get("/api/v1/auth/me").status_code == 401
