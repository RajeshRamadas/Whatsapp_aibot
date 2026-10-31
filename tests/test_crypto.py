"""Tenant encryption, tamper detection and key-rotation regression tests."""
import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from app.config import settings
from app.services.crypto import decrypt, encrypt, rewrap_master_keys, rotate_data_key, validate_configuration


def _key():
    return base64.b64encode(os.urandom(32)).decode()


def test_encryption_round_trip_isolation_and_tamper(tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', _key())
    monkeypatch.setattr(settings, 'master_key_version', 1)
    ciphertext = encrypt(tenant['tenant_id'], 'confidential-token')
    assert b'confidential-token' not in ciphertext
    assert decrypt(tenant['tenant_id'], ciphertext) == b'confidential-token'
    with pytest.raises(ValueError):
        decrypt('another-tenant', ciphertext)
    altered = bytearray(ciphertext)
    altered[-1] ^= 1
    with pytest.raises(InvalidTag):
        decrypt(tenant['tenant_id'], bytes(altered))


def test_data_and_master_key_rotation(tenant, monkeypatch):
    old_master, new_master = _key(), _key()
    monkeypatch.setattr(settings, 'master_key', old_master)
    monkeypatch.setattr(settings, 'master_key_version', 1)
    first = encrypt(tenant['tenant_id'], b'old')
    assert rotate_data_key(tenant['tenant_id']) == 2
    second = encrypt(tenant['tenant_id'], b'new')
    assert decrypt(tenant['tenant_id'], first) == b'old'
    monkeypatch.setattr(settings, 'master_key', new_master)
    monkeypatch.setattr(settings, 'master_key_version', 2)
    monkeypatch.setattr(settings, 'master_key_previous', {'1': old_master})
    assert rewrap_master_keys(tenant['tenant_id']) == 2
    monkeypatch.setattr(settings, 'master_key_previous', {})
    assert decrypt(tenant['tenant_id'], first) == b'old'
    assert decrypt(tenant['tenant_id'], second) == b'new'


def test_production_requires_valid_master_key(monkeypatch):
    monkeypatch.setattr(settings, 'environment', 'production')
    monkeypatch.setattr(settings, 'master_key', None)
    with pytest.raises(RuntimeError, match='MASTER_KEY'):
        validate_configuration()
    monkeypatch.setattr(settings, 'master_key', 'bad')
    with pytest.raises(ValueError, match='MASTER_KEY'):
        validate_configuration()
