"""Local object storage and two-step upload contract."""
import hashlib

import pytest

from app.api import auth
from app.config import settings
from app.services.storage import LocalStorage


def test_local_storage(tmp_path):
    store = LocalStorage(tmp_path)
    store.put('tenant/doc.txt', b'hello')
    assert store.get('tenant/doc.txt') == b'hello'
    assert store.presign_put('tenant/doc.txt', 'text/plain').startswith('/api/v1/')
    store.delete('tenant/doc.txt')
    with pytest.raises(FileNotFoundError):
        store.get('tenant/doc.txt')
    with pytest.raises(ValueError):
        store.put('../escape', b'bad')


def test_upload_intent_and_integrity(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    monkeypatch.setattr(settings, 'storage_backend', 'local')
    monkeypatch.setattr(settings, 'storage_local_path', str(tmp_path))
    auth._auth_hits.clear()
    assert client.post('/api/v1/auth/signup', json={
        'email': 'storage@example.com', 'name': 'Storage', 'password': 'strong-passphrase-123'
    }).status_code == 201
    assert client.post('/api/v1/auth/login', json={
        'email': 'storage@example.com', 'password': 'strong-passphrase-123'
    }).status_code == 200
    csrf = {'X-CSRF-Token': client.cookies['chatbot_csrf']}
    created = client.post('/api/v1/workspaces', json={'name': 'Storage Workspace'}, headers=csrf)
    assert created.status_code == 201
    headers = {**csrf, 'X-Workspace-Id': created.json()['tenant_id']}
    data = b'example document'
    digest = hashlib.sha256(data).hexdigest()
    body = {'filename': 'document.txt', 'content_type': 'text/plain',
            'size': len(data), 'sha256': digest}
    assert client.post('/api/v1/sources/uploads', json=body).status_code == 400
    assert client.post('/api/v1/sources/uploads', json={**body, 'size': 0},
                       headers=headers).status_code == 422
    intent = client.post('/api/v1/sources/uploads', json=body, headers=headers)
    assert intent.status_code == 201, intent.text
    url = intent.json()['upload_url']
    assert client.put(url, content=data, headers=headers).status_code == 204
    source_id = intent.json()['source_id']
    assert client.post(f'/api/v1/sources/{source_id}/complete', json={
        'size': len(data), 'sha256': '0' * 64}, headers=headers).status_code == 422
    assert client.post(f'/api/v1/sources/{source_id}/complete', json={
        'size': len(data), 'sha256': digest}, headers={**headers,
        'X-Workspace-Id': 'other'}).status_code == 404
