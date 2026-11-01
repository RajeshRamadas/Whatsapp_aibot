"""WhatsApp outbound delivery receipts: signed webhooks, ordering and tenant isolation."""
from sqlalchemy import select

from app import whatsapp
from app.db import Conversation, Message, Session
from tests.conftest import signed, wa_payload


def status_payload(state, message_id='wamid.out', phone='PNID1', errors=None):
    receipt = {'id': message_id, 'status': state}
    if errors is not None:
        receipt['errors'] = errors
    return {'entry': [{'changes': [{'value': {
        'metadata': {'phone_number_id': phone}, 'statuses': [receipt]}}]}]}


def test_status_webhooks_never_regress(client, tenant, monkeypatch):
    client.patch('/api/v1/tenant', headers=tenant['headers'],
                 json={'whatsapp_phone_number_id': 'PNID1'})
    monkeypatch.setattr(whatsapp, 'send_text', lambda *_: 'wamid.out')
    raw, headers = signed(wa_payload('hello'))
    assert client.post('/webhooks/whatsapp', content=raw, headers=headers).status_code == 200
    with Session() as db:
        outbound = db.scalar(select(Message).where(Message.channel_message_id == 'wamid.out'))
        assert outbound is not None and outbound.delivery_status == 'sent'
        message_id = outbound.id
    for state in ('delivered', 'sent', 'read', 'delivered', 'read'):
        raw, headers = signed(status_payload(state))
        assert client.post('/webhooks/whatsapp', content=raw, headers=headers).status_code == 200
    with Session() as db:
        assert db.get(Message, message_id).delivery_status == 'read'


def test_failure_code_and_wrong_tenant_receipt_ignored(client, tenant, monkeypatch):
    client.patch('/api/v1/tenant', headers=tenant['headers'],
                 json={'whatsapp_phone_number_id': 'PNID1'})
    monkeypatch.setattr(whatsapp, 'send_text', lambda *_: 'wamid.out')
    raw, headers = signed(wa_payload('hello'))
    client.post('/webhooks/whatsapp', content=raw, headers=headers)
    raw, headers = signed(status_payload('failed', phone='OTHER',
                                         errors=[{'code': 131026, 'title': 'Undeliverable'}]))
    client.post('/webhooks/whatsapp', content=raw, headers=headers)
    with Session() as db:
        outbound = db.scalar(select(Message).join(Conversation).where(
            Conversation.tenant_id == tenant['tenant_id'], Message.channel_message_id == 'wamid.out'))
        assert outbound.delivery_status == 'sent'
    raw, headers = signed(status_payload('failed',
                                         errors=[{'code': 131026, 'title': 'Undeliverable'}]))
    client.post('/webhooks/whatsapp', content=raw, headers=headers)
    with Session() as db:
        outbound = db.scalar(select(Message).where(Message.channel_message_id == 'wamid.out'))
        assert outbound.delivery_status == 'failed'
        assert outbound.error == '131026: Undeliverable'
