from datetime import timedelta

from app.config import settings
from app.db import Channel, Conversation, Message, Session, now, uid
from app.services import meta_dm_replies
from app.services.crypto import encrypt


def setup_conversation(tenant_id, *, channel='messenger', hours=1):
    with Session.begin() as db:
        channel_row = Channel(id=uid(), tenant_id=tenant_id, kind=channel, external_id='123456')
        db.add(channel_row)
        conv = Conversation(id=uid(), tenant_id=tenant_id, channel=channel, external_user='987654',
                            last_inbound_at=now() - timedelta(hours=hours))
        db.add(conv)
        return channel_row.id, conv.id


def test_meta_dm_reply_queue_and_delivery(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    monkeypatch.setattr('app.api.inbox.publish_sync', lambda *args: None)
    monkeypatch.setattr(meta_dm_replies, 'publish_sync', lambda *args: None)
    channel_id, conv_id = setup_conversation(tenant['tenant_id'])
    token = encrypt(tenant['tenant_id'], 'fake-token')
    with Session.begin() as db:
        db.get(Channel, channel_id).credentials_enc = token
    url = f'/api/v1/inbox/conversations/{conv_id}/reply'
    response = client.post(url, headers=tenant['headers'], json={'message': 'Hello Meta'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'queued'
    calls = []

    def fake_send(channel, recipient, content):
        calls.append((channel.external_id, recipient, content))
        return 'meta-out-1'

    monkeypatch.setattr(meta_dm_replies, 'send', fake_send)
    assert meta_dm_replies.dispatch_one() is True
    assert meta_dm_replies.dispatch_one() is False
    assert calls == [('123456', '987654', 'Hello Meta')]
    with Session() as db:
        message = db.get(Message, response.json()['message_id'])
        assert message.delivery_status == 'sent'
        assert message.channel_message_id == 'meta-out-1'


def test_meta_dm_window_and_disconnected_channel(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    monkeypatch.setattr('app.api.inbox.publish_sync', lambda *args: None)
    channel_id, conv_id = setup_conversation(tenant['tenant_id'], channel='instagram', hours=25)
    token = encrypt(tenant['tenant_id'], 'fake-token')
    with Session.begin() as db:
        db.get(Channel, channel_id).credentials_enc = token
    url = f'/api/v1/inbox/conversations/{conv_id}/reply'
    response = client.post(url, headers=tenant['headers'], json={'message': 'Too late'})
    assert response.status_code == 409
    assert 'meta_dm_window_expired' in response.json()['error']['message']
    with Session.begin() as db:
        db.get(Conversation, conv_id).last_inbound_at = now()
        db.get(Channel, channel_id).credentials_enc = None
    assert client.post(url, headers=tenant['headers'], json={'message': 'No channel'}).status_code == 503


def test_meta_dm_dispatch_rechecks_window(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, 'master_key', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    monkeypatch.setattr('app.api.inbox.publish_sync', lambda *args: None)
    monkeypatch.setattr(meta_dm_replies, 'publish_sync', lambda *args: None)
    channel_id, conv_id = setup_conversation(tenant['tenant_id'])
    token = encrypt(tenant['tenant_id'], 'fake-token')
    with Session.begin() as db:
        db.get(Channel, channel_id).credentials_enc = token
    response = client.post(f'/api/v1/inbox/conversations/{conv_id}/reply',
                           headers=tenant['headers'], json={'message': 'Late'})
    assert response.status_code == 200
    with Session.begin() as db:
        db.get(Conversation, conv_id).last_inbound_at = now() - timedelta(days=2)
    monkeypatch.setattr(meta_dm_replies, 'send', lambda *args: (_ for _ in ()).throw(
        AssertionError('Expired messages must never be sent')))
    assert meta_dm_replies.dispatch_one() is True
    with Session() as db:
        assert db.get(Message, response.json()['message_id']).delivery_status == 'failed'
