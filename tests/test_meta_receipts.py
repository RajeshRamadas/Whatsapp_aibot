from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db import Channel, Conversation, Message, Session, now, uid
from app.models.meta_receipts import MetaReceipt
from app.services import meta_dm_replies
from app.services import meta_receipts as service
from tests.test_meta_dm import signed
from tests.test_meta_dm_replies import setup_conversation


@pytest.fixture
def inbox(client, tenant, monkeypatch):
    events = []
    monkeypatch.setattr(service, 'publish_sync', lambda *args: events.append(args))
    channel, conv = setup_conversation(tenant['tenant_id'])
    with Session.begin() as db:
        row = db.get(Conversation, conv)
        row.status, row.handoff = 'agent', True
        message = Message(conversation_id=conv, role='agent', direction='outbound', kind='text',
            content='Private reply',
            channel_message_id='mid-1', delivery_status='sent', dispatch_started_at=now() - timedelta(seconds=4),
            meta_acknowledged_at=now() - timedelta(seconds=3))
        db.add(message)
        db.flush()
        identity = message.id
    return channel, conv, identity, events


def data(kind='messenger', status='delivered', *, mids=None, watermark=None, sender='987654', asset='123456'):
    body = {'mid': 'mid-1'} if kind == 'instagram' else (
        {'mids': ['mid-1'] if mids is None else mids} if status == 'delivered' else {})
    if watermark is not None:
        body['watermark'] = watermark
    return {'object': 'page' if kind == 'messenger' else 'instagram', 'entry': [{'messaging': [
        {'sender': {'id': sender}, 'recipient': {'id': asset},
         'read' if status == 'read' else 'delivery': body}]}]}


def post(client, payload, code=200):
    raw, headers = signed(payload, 'app-secret')
    response = client.post('/webhooks/meta-dm', content=raw, headers=headers)
    assert response.status_code == code, response.text
    return response


def due():
    with Session.begin() as db:
        for row in db.scalars(select(MetaReceipt).where(MetaReceipt.status == 'queued')):
            row.next_attempt_at = now() - timedelta(seconds=1)


@pytest.mark.parametrize('kind,status', [('instagram','read'), ('messenger','delivered'), ('messenger','read')])
def test_signed_durable_receipts_update_only_status(client, tenant, inbox, kind, status):
    channel, conv, mid, events = inbox
    with Session.begin() as db:
        db.get(Channel, channel).kind = kind
        db.get(Conversation, conv).channel = kind
        inbound_at = db.get(Conversation, conv).last_inbound_at
    payload = data(kind, status, watermark=int((now() - timedelta(seconds=1)).timestamp() * 1000))
    post(client, payload)
    post(client, payload)
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(MetaReceipt)) == 1
        assert db.get(Message, mid).delivery_status == 'sent'
    assert service.process_one() and not service.process_one()
    with Session() as db:
        assert db.get(Message, mid).delivery_status == status
        assert db.get(Conversation, conv).last_inbound_at == inbound_at
        assert db.scalar(select(func.count()).select_from(Message)) == 1
    assert events == [(tenant['tenant_id'], 'message.updated', {'conversation_id': conv})]
    response = client.get(f'/api/v1/conversations/{conv}/messages', headers=tenant['headers'])
    assert response.status_code == 200 and response.json()[0]['delivery_status'] == status


def test_read_before_delivery_and_older_watermarks_never_regress(client, inbox):
    _, conv, mid, _ = inbox
    read_at = int((now() - timedelta(seconds=1)).timestamp() * 1000)
    post(client, data(status='read', watermark=read_at))
    assert service.process_one()
    post(client, data(watermark=read_at))
    assert service.process_one()
    post(client, data(status='read', watermark=read_at - 1000))
    assert service.process_one()
    with Session() as db:
        assert db.get(Message, mid).delivery_status == 'read'
        assert int(db.get(Conversation, conv).meta_read_watermark.timestamp() * 1000) == read_at


def test_early_watermark_waits_for_mid_delivery_proof(client, inbox):
    _, _, mid, _ = inbox
    watermark = now() - timedelta(seconds=2)
    with Session.begin() as db:
        db.get(Message, mid).meta_acknowledged_at = now() - timedelta(seconds=1)
    post(client, data(status='read', watermark=int(watermark.timestamp() * 1000)))
    assert service.process_one()
    with Session() as db:
        assert db.get(Message, mid).delivery_status == 'sent'
    post(client, data(watermark=int(watermark.timestamp() * 1000)))
    assert service.process_one()
    with Session() as db:
        assert db.get(Message, mid).delivery_status == 'read'


def test_receipt_before_send_commit_retries_without_resending(client, inbox, monkeypatch):
    channel, conv, mid, _ = inbox
    with Session.begin() as db:
        db.get(Channel, channel).credentials_enc = b'fixture'
        row = db.get(Message, mid)
        row.delivery_status, row.channel_message_id, row.meta_acknowledged_at = 'queued', None, None
    calls = []
    def send(*args):
        calls.append(args)
        post(client, data())
        assert service.process_one()
        return 'mid-1'
    monkeypatch.setattr(meta_dm_replies, 'send', send)
    monkeypatch.setattr(meta_dm_replies, 'publish_sync', lambda *args: None)
    assert meta_dm_replies.dispatch_one()
    due()
    assert service.process_one()
    assert not meta_dm_replies.dispatch_one() and len(calls) == 1
    with Session() as db:
        assert db.get(Message, mid).delivery_status == 'delivered'
        assert db.get(Message, mid).meta_acknowledged_at is not None


def test_asset_customer_channel_and_tenant_isolation(client, tenant, inbox):
    channel, conv, mid, events = inbox
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other receipts'}).json()
    with Session.begin() as db:
        db.add(Channel(id=uid(), tenant_id=other['tenant_id'], kind='messenger', external_id='999999'))
        db.add(Channel(id=uid(), tenant_id=tenant['tenant_id'], kind='instagram', external_id='111111'))
    for payload in [data(asset='999999'), data(sender='wrong-customer'), data('instagram','read',asset='111111'),
                    data(asset='unconnected-asset')]:
        post(client, payload)
    while service.process_one():
        pass
    with Session() as db:
        assert db.get(Message, mid).delivery_status == 'sent' and events == []
        assert db.get(Conversation, conv).meta_read_watermark is None
        db.delete(db.get(Channel, channel))
        db.commit()
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(MetaReceipt).where(MetaReceipt.channel_id == channel)) == 0


@pytest.mark.parametrize('status', ['queued', 'sending', 'failed', 'unknown'])
def test_receipts_never_claim_unsent_or_uncertain_delivery(client, inbox, status):
    _, conv, mid, _ = inbox
    with Session.begin() as db:
        db.get(Message, mid).delivery_status = status
        db.get(Message, mid).created_at = now() - timedelta(days=3)
    post(client, data(status='read', watermark=int(now().timestamp() * 1000)))
    assert service.process_one()
    post(client, data())
    assert service.process_one()
    with Session() as db:
        assert db.get(Message, mid).delivery_status == status


def test_legacy_messages_need_mid_evidence_not_queue_time(client, inbox):
    _, _, mid, _ = inbox
    with Session.begin() as db:
        db.get(Message, mid).meta_acknowledged_at = None
    watermark = int((now() - timedelta(seconds=1)).timestamp() * 1000)
    post(client, data(status='read', watermark=watermark))
    assert service.process_one()
    with Session() as db:
        assert db.get(Message, mid).delivery_status == 'sent'
    post(client, data(watermark=watermark))
    assert service.process_one()
    with Session() as db:
        assert db.get(Message, mid).delivery_status == 'read'


def test_signature_validation_malformed_events_and_retention(client, inbox):
    _, _, mid, _ = inbox
    raw, headers = signed(data(), 'wrong-secret')
    assert client.post('/webhooks/meta-dm', content=raw, headers=headers).status_code == 401
    post(client, {'entry': None})
    for payload in [data(status='read', watermark=True), data(status='read', watermark=10**99),
                    data(status='read', watermark='invalid'), data(mids=['x' * 129]), data(mids=[1]),
                    {'object':'page','entry':[{'messaging':None}]}, {'object':'page','entry':42},
                    {'object':{}, 'entry':[]}]:
        post(client, payload)
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(MetaReceipt)) == 0
    post(client, data(mids=['not-our-mid']))
    assert service.process_one()
    with Session.begin() as db:
        row = db.scalar(select(MetaReceipt))
        row.received_at = now() - timedelta(minutes=11)
    due()
    assert service.process_one()
    with Session.begin() as db:
        row = db.scalar(select(MetaReceipt))
        assert row.status == 'ignored'
        row.received_at = now() - timedelta(days=8)
    service.cleanup()
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(MetaReceipt)) == 0
        assert db.get(Message, mid).delivery_status == 'sent'


def test_replaced_asset_cannot_apply_old_receipts(client, tenant, inbox):
    channel, conv, mid, _ = inbox
    post(client, data())
    with Session.begin() as db:
        db.delete(db.get(Channel, channel))
    replacement, _ = setup_conversation(tenant['tenant_id'])
    assert replacement != channel
    post(client, data())
    assert service.process_one()
    with Session() as db:
        assert db.get(Conversation, conv).meta_channel_id is None
        assert db.get(Message, mid).delivery_status == 'sent'


def test_impossible_delivery_watermark_never_marks_a_later_send_read(client, inbox):
    _, _, mid, _ = inbox
    watermark = int((now() - timedelta(hours=1)).timestamp() * 1000)
    post(client, data(status='read', watermark=watermark))
    assert service.process_one()
    post(client, data(watermark=watermark))
    assert service.process_one()
    with Session() as db:
        assert db.get(Message, mid).delivery_status == 'delivered'
