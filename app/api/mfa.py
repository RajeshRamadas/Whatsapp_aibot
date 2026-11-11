"""Self-service account security, independent of the currently selected workspace."""
from datetime import datetime
from typing import Annotated

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints
from sqlalchemy import select

from ..db import Session, User, UserSession, now
from ..models.mfa import MFAChallenge, UserMFA
from ..services import auth as security
from ..services import mfa
from .auth import (
    MFA_COOKIE,
    UserView,
    _active_account,
    _clear_challenge,
    _csrf,
    _rate_limit,
    _session,
    _set_cookies,
    _view,
)

router = APIRouter(prefix='/api/v1/auth/mfa', tags=['account-security'])
Proof = Annotated[str, StringConstraints(strip_whitespace=True,
    pattern=r'^(?:[0-9]{6}|[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{8}){3})$')]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PasswordInput(Input):
    password: SecretStr = Field(min_length=1, max_length=4096)


class ConfirmInput(Input):
    code: Annotated[str, StringConstraints(pattern=r'^[0-9]{6}$')]


class ManageInput(PasswordInput):
    code: Proof


class VerifyInput(Input):
    challenge: Annotated[str, StringConstraints(min_length=40, max_length=100)]
    code: Proof


class MFAStatus(BaseModel):
    enabled: bool
    eligible: bool
    configured: bool
    email_verified: bool
    recovery_remaining: int
    setup_pending: bool


class MFASetup(BaseModel):
    secret: str
    uri: str
    expires_at: datetime


class MFARecovery(BaseModel):
    recovery_codes: list[str]


class MFADisabled(BaseModel):
    status: str


def current(request, response):
    response.headers['Cache-Control'] = 'no-store'
    session, user = _session(request)
    if request.method != 'GET':
        _csrf(request, session)
        _rate_limit(request, user.id, 'mfa_manage')
    return session, user


def operation(request, response, action, *args):
    session, detached = current(request, response)
    error, result, tokens = None, None, None
    with Session.begin() as db:
        user = db.get(User, detached.id, with_for_update=True)
        if not user:
            raise HTTPException(401, 'Authentication required')
        active = db.scalar(select(UserSession).where(UserSession.id_hash == session.id_hash,
            UserSession.user_id == user.id, UserSession.expires_at > now()))
        value = db.get(UserMFA, user.id)
        if not active or value and value.enabled_at and not active.mfa_verified:
            raise HTTPException(401, 'Session expired or second factor required')
        _active_account(db, user)
        try:
            result = action(db, user, active, *args)
            if isinstance(result, tuple):
                result, tokens = result
        except mfa.MFAError as exc:
            error = exc
            mfa.log(db, user.id, 'auth.mfa_operation_failed')
        except (ValueError, InvalidTag):
            error = mfa.MFAError('Authenticator encryption is unavailable', 503)
    # Commit failed proof counters before raising the HTTP error.
    if error:
        raise HTTPException(error.status, str(error)) from None
    if tokens:
        _set_cookies(response, *tokens)
        _clear_challenge(response)
    return result


@router.get('', response_model=MFAStatus)
def status(request: Request, response: Response):
    session, user = current(request, response)
    return mfa.status(user, session.id_hash)


@router.post('/setup', response_model=MFASetup)
def setup(body: PasswordInput, request: Request, response: Response):
    return operation(request, response, mfa.enroll, body.password.get_secret_value())


@router.post('/enable', response_model=MFARecovery)
def enable(body: ConfirmInput, request: Request, response: Response):
    return operation(request, response, mfa.enable, body.code)


def clear_setup(db, user, session):
    value = db.get(UserMFA, user.id)
    if value and value.pending_enc:
        if value.pending_session != session.id_hash:
            raise mfa.MFAError('Setup not found in this session', 404)
        mfa.clear_pending(value)
        mfa.log(db, user.id, 'auth.mfa_setup_cancelled')


@router.delete('/setup', status_code=204)
def cancel_setup(request: Request, response: Response):
    operation(request, response, clear_setup)


@router.post('/recovery-codes', response_model=MFARecovery)
def regenerate(body: ManageInput, request: Request, response: Response):
    return operation(request, response, mfa.manage, body.password.get_secret_value(), body.code, 'regenerate')


@router.post('/disable', response_model=MFADisabled)
def disable(body: ManageInput, request: Request, response: Response):
    return operation(request, response, mfa.manage, body.password.get_secret_value(), body.code, 'disable')


@router.post('/verify', response_model=UserView)
def verify(body: VerifyInput, request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    raw = request.cookies.get(MFA_COOKIE, '')
    with Session() as lookup:
        account_id = lookup.scalar(select(MFAChallenge.user_id).where(
            MFAChallenge.id_hash == security.hash_token(raw))) if raw else None
    _rate_limit(request, account_id or 'unknown', 'mfa_verify')
    error, result, tokens = None, None, None
    with Session.begin() as db:
        seed = db.get(MFAChallenge, security.hash_token(raw)) if raw else None
        if not seed:
            raise HTTPException(401, 'Invalid or expired sign-in challenge')
        user = db.get(User, seed.user_id, with_for_update=True)
        if not user:
            raise HTTPException(401, 'Invalid or expired sign-in challenge')
        value = db.scalar(select(MFAChallenge).where(MFAChallenge.id_hash == security.hash_token(raw),
            MFAChallenge.user_id == user.id).with_for_update().execution_options(populate_existing=True))
        _active_account(db, user)
        try:
            mfa.finish(db, user, value, body.challenge, body.code)
            tokens = security.issue_session(db, user.id, request.client.host if request.client else None,
                                           request.headers.get('user-agent', ''), mfa_verified=True)
            result = _view(user)
            mfa.log(db, user.id, 'auth.login_succeeded')
        except mfa.MFAError as exc:
            error = exc
        except (ValueError, InvalidTag):
            error = mfa.MFAError('Authenticator encryption is unavailable', 503)
    if error:
        raise HTTPException(error.status, str(error)) from None
    _set_cookies(response, *tokens)
    _clear_challenge(response)
    return result
