"""Definitions and credentials are scoped, bounded and never returned after saving."""
import base64
import json

import pytest
from sqlalchemy import select

from app.api.shared import sha
from app.config import settings
from app.db import ApiKey, Session
from app.integrations.http_actions.contracts import Definition, arguments, extract, render
from app.models.http_actions import HttpAction
from app.services import crypto

BASE = '/api/v1/http-actions'
BODY = {'name': 'stock', 'description': 'Look up public stock', 'url_template': 'https://api.example.com/stock/{sku}',
        'parameters': [{'name': 'sku', 'description': 'Product code', 'type': 'string', 'pattern': '[A-Z0-9-]{1,20}'}],
        'outputs': {'stock': '$.stock'}, 'personal_data': False,
        'auth': {'kind': 'bearer', 'value': 'private-test-token'}}


@pytest.fixture
def action(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', base64.b64encode(b'h' * 32).decode())
    result = client.post(BASE, headers=tenant['headers'], json=BODY)
    assert result.status_code == 201, result.text
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Read', key_prefix='read', key_hash=sha('read'),
                     scopes=['viewer']))
    return result.json()


def test_create_update_pause_and_secret_isolation(client, tenant, action):
    assert action['status'] == 'needs_test' and not action['enabled']
    assert action['credential_last4'] == 'oken' and 'private-test-token' not in json.dumps(action)
    with Session() as db:
        row = db.get(HttpAction, action['id'])
        assert b'private-test-token' not in row.credentials_enc
        private = json.loads(crypto.decrypt(tenant['tenant_id'], row.credentials_enc))
        assert private['auth']['value'] == 'private-test-token'
    assert 'private-test-token' not in client.get(BASE, headers=tenant['headers']).text
    assert client.patch(BASE + '/' + action['id'], headers=tenant['headers'], json={'enabled': True}).status_code == 409
    result = client.put(BASE + '/' + action['id'], headers=tenant['headers'],
                        json={**BODY, 'description': 'New version'})
    assert result.status_code == 200 and result.json()['revision'] == 2
    assert client.post(BASE, headers=tenant['headers'], json=BODY).status_code == 409
    assert client.delete(BASE + '/' + action['id'], headers=tenant['headers']).status_code == 204


@pytest.mark.parametrize('changes', [
    {'url_template': 'http://api.example.com/x'}, {'url_template': 'https://127.0.0.1/x'},
    {'url_template': 'https://{sku}.example.com/x'}, {'url_template': 'https://[fec0::1]/x'},
    {'personal_data': True}, {'method': 'POST'}, {'outputs': {'all': '$'}},
    {'outputs': {'stock': '$..secret'}}, {'headers': {'Host': 'internal'}},
    {'auth': {'kind': 'basic', 'username': 'user'}}, {'parameters': [BODY['parameters'][0], BODY['parameters'][0]]},
    {'personal_data': True, 'identity': 'phone_match', 'identity_path': '$.phone',
     'method': 'DELETE', 'allow_changes': True}])
def test_invalid_policy(client, tenant, action, changes):
    assert client.post(BASE, headers=tenant['headers'], json={**BODY, **changes}).status_code == 422


def test_auth_validation_and_foreign_ids(client, tenant, action):
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    path = BASE + '/' + action['id']
    for method, url, body in [('get', BASE, None), ('post', BASE, BODY), ('put', path, BODY),
                              ('patch', path, {'enabled': False}), ('delete', path, None)]:
        kwargs = {'json': body} if body else {}
        call = getattr(client, method)
        assert call(url, **kwargs).status_code == 401
        assert call(url, headers={'Authorization': 'Bearer read'}, **kwargs).status_code == 403
        if url != BASE:
            assert call(url, headers={'X-Admin-Key': other['admin_key']}, **kwargs).status_code == 404
    assert client.put(BASE + '/bad', headers=tenant['headers'], json=BODY).status_code == 422
    assert client.patch(path, headers=tenant['headers'], json={}).status_code == 422
    assert client.delete(BASE + '/bad', headers=tenant['headers']).status_code == 422
    assert client.get(BASE, headers={'X-Admin-Key': other['admin_key']}).json() == []
    with Session() as db:
        assert db.scalar(select(HttpAction).where(HttpAction.tenant_id == other['tenant_id'])) is None


def test_arguments_paths_and_template_encoding():
    definition = Definition.model_validate(BODY)
    assert arguments(definition, {'sku': 'SKU-42'}) == {'sku': 'SKU-42'}
    for values in [{}, {'sku': 1}, {'sku': 'bad code'}, {'sku': 'SKU', 'customer_phone': 'other'}]:
        with pytest.raises(ValueError):
            arguments(definition, values)
    assert render('https://api.example.com/{sku}', {'sku': '../x?secret=a'}, url=True).endswith('..%2Fx%3Fsecret%3Da')
    assert render({'count': '{count}'}, {'count': 5}) == {'count': 5}
    assert extract({'rows': [{'count': 5}]}, '$.rows[0].count') == 5
