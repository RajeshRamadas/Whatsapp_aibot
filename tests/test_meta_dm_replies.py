from datetime import timedelta

import pytest

from app.config import settings
from app.db import Channel, Conversation, Message, Session, now, uid
from app.services import meta_dm_replies
from app.services.crypto import encrypt


def setup_conversation(tenant_id, *, channel='messenger', hours=1):
    with Session.begin() as db:
        channel_row = Channel(id=uid(), tenant_id=tenant_id, kind=channel, external_id='123456')
        db.add(channel_row)
        conv = Conversation(id=uid(), tenant_id=tenant_id, channel=channel, external_user='987654',
                            meta_channel_id=channel_row.id,
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


@pytest.mark.parametrize('resume', [True, False])
def test_known_token_expiry_defers_queued_replies_until_renewed(client, tenant, monkeypatch, resume):
    monkeypatch.setattr(settings, 'master_key', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    monkeypatch.setattr('app.api.inbox.publish_sync', lambda *args: None)
    monkeypatch.setattr(meta_dm_replies, 'publish_sync', lambda *args: None)
    channel_id, conv_id = setup_conversation(tenant['tenant_id'])
    token = encrypt(tenant['tenant_id'], 'fake-token')
    with Session.begin() as db:
        db.get(Channel, channel_id).credentials_enc = token
    url = f'/api/v1/inbox/conversations/{conv_id}/reply'
    response = client.post(url, headers=tenant['headers'], json={'message': 'Queued reply'})
    assert response.status_code == 200
    message_id = response.json()['message_id']
    with Session.begin() as db:
        channel = db.get(Channel, channel_id)
        channel.meta_token_status = 'expiring'
        channel.meta_token_expires_at = now() - timedelta(seconds=1)
    calls = []
    monkeypatch.setattr(meta_dm_replies, 'send', lambda *args: calls.append(args) or 'sent')
    assert client.post(url, headers=tenant['headers'], json={'message': 'New reply'}).status_code == 503
    assert not meta_dm_replies.dispatch_one()
    with Session.begin() as db:
        assert db.get(Message, message_id).delivery_status == 'queued'
        if resume:
            db.get(Channel, channel_id).meta_token_expires_at = None
        else:
            db.get(Conversation, conv_id).last_inbound_at = now() - timedelta(hours=25)
    assert meta_dm_replies.dispatch_one() and not meta_dm_replies.dispatch_one()
    assert len(calls) == int(resume)
    with Session() as db:
        assert db.get(Message, message_id).delivery_status == ('sent' if resume else 'failed')


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
