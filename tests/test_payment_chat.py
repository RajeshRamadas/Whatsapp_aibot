"""Approved tools never accept amounts; native completion notices preserve provider truth."""
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import rag, whatsapp
from app.db import AuditLog, Channel, Contact, Conversation, Message, Session, Template, Tenant, now, uid
from app.models.payments import PaymentConnection, PaymentRequest
from app.services import payment_chat, payment_notifications
from app.services import payment_processing as worker
from app.services.appointments import AppointmentError
from tests.test_payment_processing import payment as payment
from tests.test_payment_processing import post_event
from tests.test_payments import merchant as merchant


def test_bot_tool_keeps_approved_amount_and_scopes_customer(client, tenant, merchant, chats):
    connection, body, calls = merchant
    from tests.test_payments import BASE
    request = client.post(BASE + '/requests', headers=tenant['headers'], json=body).json()
    with Session() as db:
        workspace = db.get(Tenant, tenant['tenant_id'])
    tools, result = payment_chat.tools(workspace, body['conversation_id'])
    mine = next(t for t in tools if t.name == 'my_payment_requests')
    send = next(t for t in tools if t.name == 'send_payment_link')
    assert mine.handler({})[0]['amount_inr'] == '1250.50'
    assert set(send.parameters['properties']) == {'request_id'} and send.parameters['additionalProperties'] is False
    with pytest.raises(AppointmentError):
        send.handler({'request_id': uid()})
    assert payment_chat.tools(workspace, uid())[0] == []
    assert payment_chat.tools(workspace, None)[0] == []
    chats['openai'].tool_turns = [lambda results, enabled: ('', [('send_payment_link', {'request_id': request['id']})]),
                                 lambda results, enabled: ('invented amount and paid status', [])]
    answer = rag.answer(workspace, body['conversation_id'], 'Please send my payment link')
    assert answer.transactional and '1250.50' in answer.text and 'invented' not in answer.text
    assert len(calls) == 1
    with Session() as db:
        assert db.get(PaymentRequest, request['id']).amount == body['amount']
        audit = db.scalar(select(AuditLog).where(AuditLog.tenant_id == tenant['tenant_id'],
                           AuditLog.action == 'payments.tool')
                           .order_by(AuditLog.created_at.desc()))
        assert audit.details['status'] == 'success' and 'duration_ms' in audit.details


def test_web_link_and_receipt_are_durable_and_deduplicated(client, tenant, payment):
    connection, value, remote, _ = payment
    worker.process_one()
    remote.update(status='paid', amount_paid=value['amount'])
    post_event(client, connection['id'], remote)
    worker.process_event()
    post_event(client, connection['id'], remote, event_id='second-event-same-payment')
    worker.process_event()
    with Session() as db:
        row = db.get(PaymentRequest, value['id'])
        notices = db.scalars(select(Message).where(Message.conversation_id == row.conversation_id,
                                                    Message.reply_key.startswith('payment:'))).all()
        assert len(notices) == 2 and all(m.role == 'agent' and m.delivery_status == 'sent' for m in notices)
        assert any('https://rzp.io/i/demo' in m.content for m in notices)
        assert any('pay_captured123' in m.content for m in notices)
    assert not payment_notifications.process_one()


def test_whatsapp_window_template_consent_and_uncertain_delivery(client, tenant, payment, monkeypatch):
    connection, value, remote, _ = payment
    with Session.begin() as db:
        request = db.get(PaymentRequest, value['id'])
        conversation = db.get(Conversation, request.conversation_id)
        conversation.channel, conversation.external_user, conversation.last_inbound_at = (
            'whatsapp', '919999000001', now())
        db.get(Tenant, tenant['tenant_id']).whatsapp_phone_number_id = 'PAYMENT_PHONE'
        db.add(Channel(id=uid(), tenant_id=tenant['tenant_id'], kind='whatsapp', external_id='PAYMENT_PHONE'))
    worker.process_one()
    monkeypatch.setattr(whatsapp, 'send_text', lambda *a, **k: 'wamid.payment-link')
    assert payment_notifications.process_one()
    with Session.begin() as db:
        request = db.get(PaymentRequest, value['id'])
        assert db.get(Message, request.link_message_id).delivery_status == 'sent'
        conversation = db.get(Conversation, request.conversation_id)
        conversation.last_inbound_at = now() - timedelta(days=2)
        contact = Contact(id=uid(), tenant_id=tenant['tenant_id'], channel='whatsapp',
                           external_user=conversation.external_user,
                           opt_in_at=now() - timedelta(days=3), consent_source='website')
        db.add(contact)
        db.flush()
        conversation.contact_id = contact.id
        channel = db.scalar(select(Channel).where(Channel.tenant_id == tenant['tenant_id'], Channel.kind == 'whatsapp'))
        template = Template(id=uid(), channel_id=channel.id, meta_id='receipt-template', name='receipt',
                            language='en', category='UTILITY', status='APPROVED', body='Paid {{1}} INR {{2}} Ref {{3}}')
        db.add(template)
        db.flush()
        db.get(PaymentConnection, connection['id']).receipt_template_id = template.id
    remote.update(status='paid', amount_paid=value['amount'])
    post_event(client, connection['id'], remote)
    worker.process_event()
    sent = []
    def graph(*args):
        sent.append(args)
        return httpx.Response(200, json={'messages': [{'id': 'wamid.payment-receipt'}]})
    monkeypatch.setattr(whatsapp, 'graph_message', graph)
    assert payment_notifications.process_one() and sent[0][1]['type'] == 'template'
    with Session.begin() as db:
        request = db.get(PaymentRequest, value['id'])
        notice = db.get(Message, request.receipt_message_id)
        assert notice.kind == 'template' and notice.delivery_status == 'sent'
        notice.delivery_status, conversation_id = 'queued', request.conversation_id
        contact = db.get(Contact, db.get(Conversation, conversation_id).contact_id)
        contact.opt_out_at = now()
    assert payment_notifications.process_one() and len(sent) == 1
    with Session.begin() as db:
        request = db.get(PaymentRequest, value['id'])
        notice = db.get(Message, request.receipt_message_id)
        assert notice.delivery_status == 'failed'
        notice.delivery_status, notice.dispatch_started_at = 'sending', now() - timedelta(minutes=3)
    assert payment_notifications.process_one() and not payment_notifications.process_one()
    with Session() as db:
        assert db.get(Message, db.get(PaymentRequest, value['id']).receipt_message_id).delivery_status == 'unknown'
