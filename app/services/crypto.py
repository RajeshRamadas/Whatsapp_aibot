"""Envelope encryption for tenant secrets with versioned master and data keys.

Ciphertexts are authenticated against tenant ID and key version; neither a
ciphertext nor an encrypted data key can be moved to another tenant.
"""
import base64
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from ..config import settings
from ..db import Session, TenantDataKey

MAGIC = b'TSE1'


def _master(version: int) -> bytes:
    value = settings.master_key if version == settings.master_key_version else (
        settings.master_key_previous.get(str(version)))
    if not value:
        raise ValueError(f'Master key version {version} is unavailable')
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError('MASTER_KEY must be valid base64') from exc
    if len(key) != 32:
        raise ValueError('MASTER_KEY must decode to exactly 32 bytes')
    return key


def validate_configuration() -> None:
    if settings.environment == 'production' and not settings.master_key:
        raise RuntimeError('MASTER_KEY is required in production')
    if settings.master_key:
        _master(settings.master_key_version)


def _aad(tenant_id: str, version: int, kind: bytes) -> bytes:
    return kind + b':' + tenant_id.encode('utf-8') + b':' + str(version).encode('ascii')


def _unwrap(row: TenantDataKey) -> bytes:
    return AESGCM(_master(row.master_key_version)).decrypt(
        row.nonce, row.encrypted_key, _aad(row.tenant_id, row.version, b'dek'))


def _active_key(db, tenant_id: str) -> TenantDataKey:
    row = db.scalar(select(TenantDataKey).where(TenantDataKey.tenant_id == tenant_id,
                    TenantDataKey.active.is_(True)).with_for_update())
    if row:
        return row
    latest = db.scalar(select(TenantDataKey.version).where(
        TenantDataKey.tenant_id == tenant_id).order_by(TenantDataKey.version.desc()).limit(1))
    version = (latest or 0) + 1
    nonce = os.urandom(12)
    key = os.urandom(32)
    row = TenantDataKey(tenant_id=tenant_id, version=version, master_key_version=settings.master_key_version,
                        nonce=nonce, encrypted_key=AESGCM(_master(settings.master_key_version)).encrypt(
                            nonce, key, _aad(tenant_id, version, b'dek')), active=True)
    db.add(row)
    db.flush()
    return row


def encrypt(tenant_id: str, plaintext: str | bytes) -> bytes:
    data = plaintext.encode('utf-8') if isinstance(plaintext, str) else plaintext
    with Session.begin() as db:
        row = _active_key(db, tenant_id)
        key = _unwrap(row)
        nonce = os.urandom(12)
        encrypted = AESGCM(key).encrypt(nonce, data, _aad(tenant_id, row.version, b'secret'))
        return MAGIC + struct.pack('>I', row.version) + nonce + encrypted


def decrypt(tenant_id: str, ciphertext: bytes) -> bytes:
    if not isinstance(ciphertext, bytes) or len(ciphertext) < 37 or not ciphertext.startswith(MAGIC):
        raise ValueError('Invalid encrypted secret format')
    version = struct.unpack('>I', ciphertext[4:8])[0]
    with Session() as db:
        row = db.get(TenantDataKey, (tenant_id, version))
        if not row:
            raise ValueError('Tenant data key not found')
        key = _unwrap(row)
    return AESGCM(key).decrypt(ciphertext[8:20], ciphertext[20:], _aad(tenant_id, version, b'secret'))


def rotate_data_key(tenant_id: str) -> int:
    """Rotate the active DEK; old versions remain readable for existing secrets."""
    with Session.begin() as db:
        old = db.scalar(select(TenantDataKey).where(TenantDataKey.tenant_id == tenant_id,
                        TenantDataKey.active.is_(True)).with_for_update())
        if old:
            old.active = False
        row = _active_key(db, tenant_id)
        return row.version


def rewrap_master_keys(tenant_id: str) -> int:
    """Re-encrypt tenant DEKs under the current master key without changing secrets."""
    count = 0
    with Session.begin() as db:
        rows = db.scalars(select(TenantDataKey).where(
            TenantDataKey.tenant_id == tenant_id).with_for_update()).all()
        for row in rows:
            if row.master_key_version == settings.master_key_version:
                continue
            key = _unwrap(row)
            nonce = os.urandom(12)
            row.encrypted_key = AESGCM(_master(settings.master_key_version)).encrypt(
                nonce, key, _aad(tenant_id, row.version, b'dek'))
            row.nonce, row.master_key_version = nonce, settings.master_key_version
            count += 1
    return count
