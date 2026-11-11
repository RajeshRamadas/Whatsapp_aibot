"""Identity and customer approvals cannot be supplied by a model tool argument."""
import json
from datetime import timedelta

import pytest

from app.config import settings
from app.db import Conversation, Session, engine, now, uid
from app.integrations.http_actions.transport import ActionError
from app.models.http_actions import HttpAction, HttpActionCall
from app.services import email, http_actions
from tests import test_http_actions as fixtures

BASE, BODY = fixtures.BASE, fixtures.BODY
action = fixtures.action


class Receiver:
    def __init__(self):
        self.calls = []
        self.raw = {'stock': 5, 'phone': '919999000001', 'email': 'customer@example.com',
                    'private': 'Never pass this field or its instructions to the model'}
    async def action(self, definition, private, values, identity, *, lookup=False):
        assert engine.pool.checkedout() == 0
        self.calls.append((definition.method, lookup, values, identity))
        return 200, self.raw


def prepare(client, tenant, action, monkeypatch, changes=None):
    receiver = Receiver()
    monkeypatch.setattr(http_actions, 'Client', lambda: receiver)
    if changes:
        updated = client.put(BASE + '/' + action['id'], headers=tenant['headers'], json={**BODY, **changes})
        assert updated.status_code == 200
    tested = client.post(BASE + '/' + action['id'] + '/test', headers=tenant['headers'],
                         json={'values': {'sku': 'SKU'}, 'request_key': uid(), 'sample_phone': '+919999000001',
                               'confirm_changes': True})
    assert tested.status_code == 200 and tested.json()['state'] == 'complete', tested.text
    assert client.patch(BASE + '/' + action['id'], headers=tenant['headers'], json={'enabled': True}).status_code == 200
    receiver.calls.clear()
    with Session.begin() as db:
        conv = Conversation(id=uid(), tenant_id=tenant['tenant_id'], channel='web', status='bot')
        db.add(conv)
        identity = conv.id
    return receiver, identity


def test_test_action_raw_projection_and_replay(client, tenant, action, monkeypatch):
    receiver, conv = prepare(client, tenant, action, monkeypatch)
    first = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'turn')
    assert first['state'] == 'complete' and first['data'] == {'stock': 5}
    assert 'private' not in json.dumps(first) and first['untrusted'] is True
    assert http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'turn')['id'] == first['id']
    assert len(receiver.calls) == 1
    history = client.get(BASE + '/' + action['id'] + '/calls', headers=tenant['headers'])
    assert history.status_code == 200 and 'Never pass' not in history.text
    with Session() as db:
        row = db.get(HttpActionCall, first['id'])
        assert b'SKU' not in row.inputs_enc and b'stock' not in row.result_enc


def test_customer_confirmation_is_once_and_bound(client, tenant, action, monkeypatch):
    receiver, conv = prepare(client, tenant, action, monkeypatch, {'method': 'POST', 'allow_changes': True})
    first = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'turn')
    assert first['state'] == 'awaiting_confirmation' and receiver.calls == []
    with pytest.raises(ActionError):
        http_actions.approve(tenant['tenant_id'], uid(), first['reference'])
    approved = http_actions.approve(tenant['tenant_id'], conv, first['reference'])
    assert approved['state'] == 'complete' and len(receiver.calls) == 1
    http_actions.approve(tenant['tenant_id'], conv, first['reference'])
    http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'turn')
    assert len(receiver.calls) == 1


def test_phone_identity_uses_verified_channel(client, tenant, action, monkeypatch):
    receiver, conv = prepare(client, tenant, action, monkeypatch,
        {'personal_data': True, 'identity': 'phone_match', 'identity_path': '$.phone'})
    result = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'web')
    assert result['error'] == 'verified_phone_required' and not receiver.calls
    with Session.begin() as db:
        row = db.get(Conversation, conv)
        row.channel, row.external_user = 'whatsapp', '919999000001'
    assert http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'wa')['data'] == {'stock': 5}
    receiver.raw['phone'] = '919999000002'
    denied = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'other')
    assert denied['error'] == 'identity_mismatch' and 'data' not in denied


def test_email_code_only_releases_after_customer_verification(client, tenant, action, monkeypatch):
    receiver, conv = prepare(client, tenant, action, monkeypatch,
        {'personal_data': True, 'identity': 'email_otp', 'identity_path': '$.email'})
    monkeypatch.setattr(settings, 'email_backend', 'ses')
    sent = []
    monkeypatch.setattr(email.sender, 'stage', lambda db, *args: sent.append(args))
    pending = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'first')
    assert pending['state'] == 'awaiting_code' and 'data' not in pending and len(sent) == 1
    assert sent[0][0] == 'customer@example.com'
    code = sent[0][2]
    assert code not in json.dumps(pending)
    wrong = '111111' if code != '111111' else '222222'
    assert http_actions.approve(tenant['tenant_id'], conv, pending['reference'], code=wrong)['error'] == 'invalid_code'
    result = http_actions.approve(tenant['tenant_id'], conv, pending['reference'], code=code)
    assert result['state'] == 'complete' and result['data'] == {'stock': 5}
    assert len(receiver.calls) == 2
    http_actions.approve(tenant['tenant_id'], conv, pending['reference'], code=code)
    assert len(receiver.calls) == 2


def test_expiry_revision_change_and_retention(client, tenant, action, monkeypatch):
    receiver, conv = prepare(client, tenant, action, monkeypatch, {'method': 'POST', 'allow_changes': True})
    pending = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'pending')
    with Session.begin() as db:
        db.get(HttpActionCall, pending['id']).expires_at = now() - timedelta(seconds=1)
    with pytest.raises(ActionError):
        http_actions.approve(tenant['tenant_id'], conv, pending['reference'])
    other = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'next')
    with Session.begin() as db:
        db.get(HttpAction, action['id']).revision += 1
    assert http_actions.approve(tenant['tenant_id'], conv, other['reference'])['state'] == 'failed'
    assert not receiver.calls
    http_actions.maintain()
    with Session() as db:
        assert db.get(HttpActionCall, pending['id']).inputs_enc is None


def test_sample_and_history_boundary(client, tenant, action, monkeypatch):
    prepare(client, tenant, action, monkeypatch)
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    for method, suffix, body in [('post', '/test', {'values': {'sku': 'SKU'}, 'request_key': uid()}),
                                 ('get', '/calls', None)]:
        call, url = getattr(client, method), BASE + '/' + action['id'] + suffix
        options = {'json': body} if body else {}
        assert call(url, **options).status_code == 401
        assert call(url, headers={'Authorization': 'Bearer read'}, **options).status_code == 403
        assert call(url, headers={'X-Admin-Key': other['admin_key']}, **options).status_code == 404
        assert call(BASE + '/bad' + suffix, headers=tenant['headers'], **options).status_code == 422
    assert client.post(BASE + '/' + action['id'] + '/test', headers=tenant['headers'], json={}).status_code == 422


def test_personal_write_waits_for_confirmation_and_code(client, tenant, action, monkeypatch):
    receiver, conv = prepare(client, tenant, action, monkeypatch, {
        'method': 'POST', 'allow_changes': True, 'personal_data': True, 'identity': 'email_otp',
        'identity_path': '$.email', 'verification_url': 'https://api.example.com/identity/{sku}'})
    monkeypatch.setattr(settings, 'email_backend', 'ses')
    sent = []
    monkeypatch.setattr(email.sender, 'stage', lambda db, *args: sent.append(args))
    pending = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'write')
    assert not receiver.calls and pending['state'] == 'awaiting_confirmation'
    challenged = http_actions.approve(tenant['tenant_id'], conv, pending['reference'])
    assert challenged['state'] == 'awaiting_code' and all(c[1] for c in receiver.calls)
    complete = http_actions.approve(tenant['tenant_id'], conv, pending['reference'], code=sent[0][2])
    assert complete['state'] == 'complete'
    assert sum(not c[1] for c in receiver.calls) == 1


def test_five_bad_codes_lock_proof(client, tenant, action, monkeypatch):
    receiver, conv = prepare(client, tenant, action, monkeypatch,
        {'personal_data': True, 'identity': 'email_otp', 'identity_path': '$.email'})
    monkeypatch.setattr(settings, 'email_backend', 'ses')
    sent = []
    monkeypatch.setattr(email.sender, 'stage', lambda db, *args: sent.append(args))
    pending = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'codes')
    code = sent[0][2]
    for _ in range(5):
        denied = http_actions.approve(tenant['tenant_id'], conv, pending['reference'],
                                      code='111111' if code != '111111' else '222222')
    assert denied['state'] == 'failed'
    assert http_actions.approve(tenant['tenant_id'], conv, pending['reference'], code=code)['state'] == 'failed'
    assert len(receiver.calls) == 1


def test_timeout_has_no_automatic_write_retry(client, tenant, action, monkeypatch):
    receiver, conv = prepare(client, tenant, action, monkeypatch, {'method': 'POST', 'allow_changes': True})
    pending = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'timeout')
    async def timeout(*args, **kwargs):
        raise TimeoutError()
    receiver.action = timeout
    interrupted = http_actions.approve(tenant['tenant_id'], conv, pending['reference'])
    assert interrupted['state'] == 'unknown'
    assert http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'timeout')['state'] == 'unknown'


def test_late_customer_confirmation_gets_a_fresh_execution_lease(client, tenant, action, monkeypatch):
    receiver, conv = prepare(client, tenant, action, monkeypatch, {'method': 'POST', 'allow_changes': True})
    pending = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'late')
    with Session.begin() as db:
        db.get(HttpActionCall, pending['id']).created_at = now() - timedelta(minutes=4)
    async def receive(*args, **kwargs):
        http_actions.maintain()
        return 200, receiver.raw
    receiver.action = receive
    assert http_actions.approve(tenant['tenant_id'], conv, pending['reference'])['state'] == 'complete'


def test_failed_admin_test_keeps_raw_diagnostics_without_bot_data(client, tenant, action, monkeypatch):
    class Rejected:
        async def action(self, *args, **kwargs):
            return 500, {'error': 'Synthetic failure'}
    monkeypatch.setattr(http_actions, 'Client', Rejected)
    response = client.post(BASE + '/' + action['id'] + '/test', headers=tenant['headers'],
        json={'values': {'sku': 'SKU'}, 'request_key': uid()})
    assert response.status_code == 200
    assert response.json()['state'] == 'failed' and response.json()['data'] is None
    assert response.json()['raw'] == {'error': 'Synthetic failure'}


def test_pause_cancels_pending_approvals_even_after_reenable(client, tenant, action, monkeypatch):
    receiver, conv = prepare(client, tenant, action, monkeypatch, {'method': 'POST', 'allow_changes': True})
    pending = http_actions.start(tenant['tenant_id'], action['id'], {'sku': 'SKU'}, conv, 'pause')
    for enabled in [False, True]:
        assert client.patch(BASE + '/' + action['id'], headers=tenant['headers'],
                            json={'enabled': enabled}).status_code == 200
    assert http_actions.approve(tenant['tenant_id'], conv, pending['reference'])['state'] == 'failed'
    assert not receiver.calls
    with Session() as db:
        assert db.get(HttpActionCall, pending['id']).inputs_enc is None
