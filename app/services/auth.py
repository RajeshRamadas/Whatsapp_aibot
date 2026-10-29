"""Password and session primitives. Raw tokens never enter the database."""
import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from ..db import now

SESSION_DAYS = 14
CODE_MINUTES = 15
COMMON = {'password123', '1234567890', 'qwerty12345', 'password1234', '12345678910'}
_hasher = PasswordHasher()


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    if len(password) < 10 or password.lower() in COMMON:
        raise ValueError('Choose a stronger password (at least 10 characters)')
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerifyMismatchError, VerificationError):
        return False


def session_expiry():
    return now() + timedelta(days=SESSION_DAYS)


def code_expiry():
    return now() + timedelta(minutes=CODE_MINUTES)
