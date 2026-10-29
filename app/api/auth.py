"""User registration and cookie-session authentication."""
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select

from ..config import settings
from ..db import AuthCode, Session, User, UserSession, now, uid
from ..services import auth as security
from ..services import email

router = APIRouter(prefix='/api/v1/auth', tags=['auth'])
_auth_hits = defaultdict(deque)
_auth_lock = threading.Lock()
COOKIE = 'chatbot_session'
CSRF_COOKIE = 'chatbot_csrf'


class Signup(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10)
    name: str = Field(min_length=1, max_length=200)


class Credentials(BaseModel):
    email: EmailStr
    password: str


class CodeInput(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r'^\d{6})


class ResetRequest(BaseModel):
    email: EmailStr


class ResetConfirm(CodeInput):
    password: str = Field(min_length=10)


def _email(value: str) -> str:
    return value.strip().lower()


def _view(user: User) -> dict:
    return {'id': user.id, 'email': user.email, 'name': user.name,
            'email_verified': user.email_verified_at is not None}


def _issue_code(db, user: User, purpose: str):
    db.execute(delete(AuthCode).where(AuthCode.user_id == user.id, AuthCode.purpose == purpose))
    code = f'{secrets.randbelow(1000000):06d}'
    db.add(AuthCode(id=uid(), user_id=user.id, purpose=purpose,
                    code_hash=security.hash_token(code), expires_at=security.code_expiry()))
    return code


def _session(request: Request) -> tuple[UserSession, User]:
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401, 'Authentication required')
    with Session.begin() as db:
        row = db.get(UserSession, security.hash_token(raw))
        if not row or row.expires_at <= now():
            raise HTTPException(401, 'Session expired or revoked')
        user = db.get(User, row.user_id)
        if not user:
            raise HTTPException(401, 'Authentication required')
        row.expires_at = security.session_expiry()
        db.flush()
        db.expunge(row)
        db.expunge(user)
    return row, user


def _csrf(request: Request, row: UserSession):
    header = request.headers.get('X-CSRF-Token', '')
    cookie = request.cookies.get(CSRF_COOKIE, '')
    if not header or not cookie or not secrets.compare_digest(header, cookie) or not secrets.compare_digest(
            security.hash_token(header), row.csrf_hash):
        raise HTTPException(403, 'Invalid CSRF token')


def _set_cookies(response: Response, raw: str, csrf: str):
    age = security.SESSION_DAYS * 86400
    response.set_cookie(COOKIE, raw, httponly=True, secure=settings.auth_cookie_secure,
                        samesite='lax', max_age=age)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=settings.auth_cookie_secure,
                        samesite='lax', max_age=age)


def _rate_limit(request: Request, email_address: str, action: str):
    ip = request.client.host if request.client else 'unknown'
    timestamp = time.monotonic()
    with _auth_lock:
        for key in (f'{action}:ip:{ip}', f'{action}:email:{email_address}'):
            hits = _auth_hits[key]
            while hits and hits[0] <= timestamp - 900:
                hits.popleft()
            if len(hits) >= 5:
                raise HTTPException(429, 'Too many attempts. Try again later.')
        for key in (f'{action}:ip:{ip}', f'{action}:email:{email_address}'):
            _auth_hits[key].append(timestamp)


@router.post('/signup', status_code=201)
def signup(body: Signup):
    try:
        password_hash = security.hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    address = _email(str(body.email))
    with Session.begin() as db:
        if db.scalar(select(User.id).where(User.email == address)):
            raise HTTPException(409, 'Email already registered')
        user = User(id=uid(), email=address, password_hash=password_hash, name=body.name,
                    is_platform_admin=False)
        db.add(user)
        db.flush()
        code = _issue_code(db, user, 'verify')
        view = _view(user)
    email.sender.send(address, 'Verify your email', code)
    return view


@router.post('/login')
def login(body: Credentials, request: Request, response: Response):
    address = _email(str(body.email))
    _rate_limit(request, address, 'login')
    with Session.begin() as db:
        user = db.scalar(select(User).where(User.email == address))
        if not user or not security.verify_password(user.password_hash, body.password):
            raise HTTPException(401, 'Invalid credentials')
        raw, csrf = security.new_token(), security.new_token()
        db.add(UserSession(id_hash=security.hash_token(raw), user_id=user.id,
                           csrf_hash=security.hash_token(csrf), expires_at=security.session_expiry(),
                           ip=request.client.host if request.client else None,
                           user_agent=request.headers.get('user-agent', '')[:512]))
        view = _view(user)
    _set_cookies(response, raw, csrf)
    return view


@router.get('/me')
def me(request: Request, response: Response):
    row, user = _session(request)
    raw = request.cookies[COOKIE]
    csrf = request.cookies.get(CSRF_COOKIE, '')
    if csrf and secrets.compare_digest(security.hash_token(csrf), row.csrf_hash):
        _set_cookies(response, raw, csrf)
    return _view(user)


@router.post('/logout', status_code=204)
def logout(request: Request, response: Response):
    row, _ = _session(request)
    _csrf(request, row)
    with Session.begin() as db:
        db.execute(delete(UserSession).where(UserSession.id_hash == row.id_hash))
    response.delete_cookie(COOKIE)
    response.delete_cookie(CSRF_COOKIE)


def _consume_code(db, address: str, code: str, purpose: str) -> User:
    user = db.scalar(select(User).where(User.email == address))
    if not user:
        raise HTTPException(400, 'Invalid or expired code')
    record = db.scalar(select(AuthCode).where(AuthCode.user_id == user.id, AuthCode.purpose == purpose))
    if not record or record.expires_at <= now() or not secrets.compare_digest(
            record.code_hash, security.hash_token(code)):
        raise HTTPException(400, 'Invalid or expired code')
    db.delete(record)
    return user


@router.post('/verify-email')
def verify_email(body: CodeInput):
    with Session.begin() as db:
        user = _consume_code(db, _email(str(body.email)), body.code, 'verify')
        user.email_verified_at = now()
    return {'status': 'verified'}


@router.post('/password-reset/request')
def password_reset_request(body: ResetRequest, request: Request):
    address = _email(str(body.email))
    _rate_limit(request, address, 'reset')
    with Session.begin() as db:
        user = db.scalar(select(User).where(User.email == address))
        code = _issue_code(db, user, 'reset') if user else None
    if code:
        email.sender.send(address, 'Reset your password', code)
    return {'status': 'accepted'}


@router.post('/password-reset/confirm')
def password_reset_confirm(body: ResetConfirm):
    try:
        password_hash = security.hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    with Session.begin() as db:
        user = _consume_code(db, _email(str(body.email)), body.code, 'reset')
        user.password_hash = password_hash
        db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    return {'status': 'updated'}
)


class ResetRequest(BaseModel):
    email: EmailStr


class ResetConfirm(CodeInput):
    password: str = Field(min_length=10)


def _email(value: str) -> str:
    return value.strip().lower()


def _view(user: User) -> dict:
    return {'id': user.id, 'email': user.email, 'name': user.name,
            'email_verified': user.email_verified_at is not None}


def _issue_code(db, user: User, purpose: str):
    db.execute(delete(AuthCode).where(AuthCode.user_id == user.id, AuthCode.purpose == purpose))
    code = f'{secrets.randbelow(1000000):06d}'
    db.add(AuthCode(id=uid(), user_id=user.id, purpose=purpose,
                    code_hash=security.hash_token(code), expires_at=security.code_expiry()))
    return code


def _session(request: Request) -> tuple[UserSession, User]:
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401, 'Authentication required')
    with Session.begin() as db:
        row = db.get(UserSession, security.hash_token(raw))
        if not row or row.expires_at <= now():
            raise HTTPException(401, 'Session expired or revoked')
        user = db.get(User, row.user_id)
        if not user:
            raise HTTPException(401, 'Authentication required')
        row.expires_at = security.session_expiry()
        db.flush()
        db.expunge(row)
        db.expunge(user)
    return row, user


def _csrf(request: Request, row: UserSession):
    header = request.headers.get('X-CSRF-Token', '')
    cookie = request.cookies.get(CSRF_COOKIE, '')
    if not header or not cookie or not secrets.compare_digest(header, cookie) or not secrets.compare_digest(
            security.hash_token(header), row.csrf_hash):
        raise HTTPException(403, 'Invalid CSRF token')


def _set_cookies(response: Response, raw: str, csrf: str):
    age = security.SESSION_DAYS * 86400
    response.set_cookie(COOKIE, raw, httponly=True, secure=settings.auth_cookie_secure,
                        samesite='lax', max_age=age)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=settings.auth_cookie_secure,
                        samesite='lax', max_age=age)


def _rate_limit(request: Request, email_address: str, action: str):
    ip = request.client.host if request.client else 'unknown'
    timestamp = time.monotonic()
    with _auth_lock:
        for key in (f'{action}:ip:{ip}', f'{action}:email:{email_address}'):
            hits = _auth_hits[key]
            while hits and hits[0] <= timestamp - 900:
                hits.popleft()
            if len(hits) >= 5:
                raise HTTPException(429, 'Too many attempts. Try again later.')
        for key in (f'{action}:ip:{ip}', f'{action}:email:{email_address}'):
            _auth_hits[key].append(timestamp)


@router.post('/signup', status_code=201)
def signup(body: Signup):
    try:
        password_hash = security.hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    address = _email(str(body.email))
    with Session.begin() as db:
        if db.scalar(select(User.id).where(User.email == address)):
            raise HTTPException(409, 'Email already registered')
        user = User(id=uid(), email=address, password_hash=password_hash, name=body.name,
                    is_platform_admin=False)
        db.add(user)
        db.flush()
        code = _issue_code(db, user, 'verify')
        view = _view(user)
    email.sender.send(address, 'Verify your email', code)
    return view


@router.post('/login')
def login(body: Credentials, request: Request, response: Response):
    address = _email(str(body.email))
    _rate_limit(request, address, 'login')
    with Session.begin() as db:
        user = db.scalar(select(User).where(User.email == address))
        if not user or not security.verify_password(user.password_hash, body.password):
            raise HTTPException(401, 'Invalid credentials')
        raw, csrf = security.new_token(), security.new_token()
        db.add(UserSession(id_hash=security.hash_token(raw), user_id=user.id,
                           csrf_hash=security.hash_token(csrf), expires_at=security.session_expiry(),
                           ip=request.client.host if request.client else None,
                           user_agent=request.headers.get('user-agent', '')[:512]))
        view = _view(user)
    _set_cookies(response, raw, csrf)
    return view


@router.get('/me')
def me(request: Request, response: Response):
    row, user = _session(request)
    raw = request.cookies[COOKIE]
    csrf = request.cookies.get(CSRF_COOKIE, '')
    if csrf and secrets.compare_digest(security.hash_token(csrf), row.csrf_hash):
        _set_cookies(response, raw, csrf)
    return _view(user)


@router.post('/logout', status_code=204)
def logout(request: Request, response: Response):
    row, _ = _session(request)
    _csrf(request, row)
    with Session.begin() as db:
        db.execute(delete(UserSession).where(UserSession.id_hash == row.id_hash))
    response.delete_cookie(COOKIE)
    response.delete_cookie(CSRF_COOKIE)


def _consume_code(db, address: str, code: str, purpose: str) -> User:
    user = db.scalar(select(User).where(User.email == address))
    if not user:
        raise HTTPException(400, 'Invalid or expired code')
    record = db.scalar(select(AuthCode).where(AuthCode.user_id == user.id, AuthCode.purpose == purpose))
    if not record or record.expires_at <= now() or not secrets.compare_digest(
            record.code_hash, security.hash_token(code)):
        raise HTTPException(400, 'Invalid or expired code')
    db.delete(record)
    return user


@router.post('/verify-email')
def verify_email(body: CodeInput):
    with Session.begin() as db:
        user = _consume_code(db, _email(str(body.email)), body.code, 'verify')
        user.email_verified_at = now()
    return {'status': 'verified'}


@router.post('/password-reset/request')
def password_reset_request(body: ResetRequest, request: Request):
    address = _email(str(body.email))
    _rate_limit(request, address, 'reset')
    with Session.begin() as db:
        user = db.scalar(select(User).where(User.email == address))
        code = _issue_code(db, user, 'reset') if user else None
    if code:
        email.sender.send(address, 'Reset your password', code)
    return {'status': 'accepted'}


@router.post('/password-reset/confirm')
def password_reset_confirm(body: ResetConfirm):
    try:
        password_hash = security.hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    with Session.begin() as db:
        user = _consume_code(db, _email(str(body.email)), body.code, 'reset')
        user.password_hash = password_hash
        db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    return {'status': 'updated'}
