import hashlib
import hmac
import json

from app.api import meta_dm
from app.db import Channel, Conversation, Message, MetaDmEvent, Session, uid
from app.services.meta_dm import process_one


def payload(kind='page', mid='m1', recipient='page-123'):
    return {'object': kind, 'entry': [{'messaging': [
        {'sender': {'id': 'customer-1'}, 'recipient': {'id': recipient},
         'message': {'mid': mid, 'text': 'Hi there'}}]}]}


def signed(data, secret):
    raw = json.dumps(data).encode()
    return raw, {'x-hub-signature-256': 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
                 'content-type': 'application/json'}


def test_webhook_signature_dedupe_and_tenant_isolation(client, tenant, monkeypatch):
    monkeypatch.setattr(meta_dm.settings, 'whatsapp_app_secret', 'dm-test-secret')
    monkeypatch.setattr('app.services.meta_dm.publish_sync', lambda *args: None)
    with Session.begin() as db:
        db.add(Channel(id=uid(), tenant_id=tenant['tenant_id'], kind='messenger', external_id='page-123'))
    url = '/webhooks/meta-dm'
    raw, headers = signed(payload(), 'dm-test-secret')
    assert client.post(url, content=raw).status_code == 401
    assert client.post(url, content=raw, headers=headers).status_code == 200
    assert client.post(url, content=raw, headers=headers).status_code == 200
    with Session() as db:
        rows = db.query(MetaDmEvent).all()
        assert len(rows) == 1
        assert rows[0].tenant_id == tenant['tenant_id']
    assert process_one() is True
    assert process_one() is False
    with Session() as db:
        conv = db.query(Conversation).filter_by(tenant_id=tenant['tenant_id'], channel='messenger').one()
        assert db.query(Message).filter_by(conversation_id=conv.id).count() == 1
    raw, headers = signed(payload(mid='m2', recipient='unknown-page'), 'dm-test-secret')
    assert client.post(url, content=raw, headers=headers).status_code == 200
    with Session() as db:
        assert db.query(MetaDmEvent).count() == 1


def test_instagram_echo_and_invalid_payload(client, tenant, monkeypatch):
    monkeypatch.setattr(meta_dm.settings, 'whatsapp_app_secret', 'dm-test-secret')
    with Session.begin() as db:
        db.add(Channel(id=uid(), tenant_id=tenant['tenant_id'], kind='instagram', external_id='ig-123'))
    data = payload('instagram', 'ig1', 'ig-123')
    data['entry'][0]['messaging'][0]['message']['is_echo'] = True
    raw, headers = signed(data, 'dm-test-secret')
    assert client.post('/webhooks/meta-dm', content=raw, headers=headers).status_code == 200
    with Session() as db:
        assert db.query(MetaDmEvent).count() == 0
    assert client.post('/webhooks/meta-dm', content=b'{', headers=headers).status_code == 401
    raw, headers = signed(payload('instagram', 'ig2', 'ig-123'), 'dm-test-secret')
    assert client.post('/webhooks/meta-dm', content=raw, headers=headers).status_code == 200
    with Session() as db:
        assert db.query(MetaDmEvent).count() == 1
