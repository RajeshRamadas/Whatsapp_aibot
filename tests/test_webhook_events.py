"""All subscribed domain transitions stage metadata atomically, without leaking content."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import Campaign, CampaignRecipient, Channel, Contact, Conversation, Message, Session, now, uid
from app.models.appointments import Booking, Service, Slot, Staff
from app.models.payments import PaymentConnection, PaymentRequest
from app.models.webhooks import WebhookDelivery, WebhookEndpoint
from app.services import campaign_delivery, leads
from app.services.outgoing_webhooks import EVENTS
from tests import test_outgoing_webhooks as fixtures

endpoint = fixtures.endpoint


@pytest.fixture
def subscribed(tenant, endpoint):
    with Session.begin() as db:
        ep = db.get(WebhookEndpoint, endpoint['id'])
        ep.events, ep.enabled = sorted(EVENTS), True
        conversation = Conversation(id=uid(), tenant_id=tenant['tenant_id'], channel='web')
        db.add(conversation)
        db.flush()
        return conversation.id


def payloads(tenant):
    with Session() as db:
        return [d.payload for d in db.scalars(select(WebhookDelivery).where(
            WebhookDelivery.tenant_id == tenant['tenant_id']))]


def test_messages_handoff_leads_and_closed(client, tenant, endpoint, subscribed):
    with Session.begin() as db:
        conv = db.get(Conversation, subscribed)
        db.add_all([Message(conversation_id=conv.id, role='user', content='Private incoming text'),
                    Message(conversation_id=conv.id, role='agent', kind='note', content='Private note')])
    assert [p['type'] for p in payloads(tenant)] == ['message.received']
    with Session.begin() as db:
        conv = db.get(Conversation, subscribed)
        outgoing = Message(conversation_id=conv.id, role='agent', direction='outbound', content='Private reply',
                           delivery_status='queued')
        db.add(outgoing)
        db.flush()
        outgoing_id = outgoing.id
        assert len(payloads(tenant)) == 1
        outgoing.delivery_status = 'sent'
        conv.status, conv.handoff = 'agent', True
        leads.save_lead(db, conv, {'name': 'Private customer', 'phone': '+919999000001'})
    with Session.begin() as db:
        conv = db.get(Conversation, subscribed)
        conv.handoff = True  # No-op assignments and delivery receipts must not duplicate events.
        db.get(Message, outgoing_id).delivery_status = 'read'
        assert not leads.save_lead(db, conv, {'name': 'Private customer'})
        assert not leads.save_lead(db, conv, {'need': 'Private need'})
        conv.status = 'closed'
    events = payloads(tenant)
    assert sorted(p['type'] for p in events) == sorted([
        'message.received', 'message.sent', 'conversation.handoff', 'conversation.closed',
        'lead.created', 'lead.updated'])
    raw = json.dumps(events)
    for secret in ['Private incoming text', 'Private note', 'Private reply', 'Private customer', '+919999000001']:
        assert secret not in raw
    assert all(p['tenant_id'] == tenant['tenant_id'] for p in events)


def test_confirmed_booking_verified_payment_and_campaign(client, tenant, endpoint, subscribed):
    tid, future = tenant['tenant_id'], now() + timedelta(days=2)
    with Session.begin() as db:
        service = Service(tenant_id=tid, name='Private service', duration_minutes=30)
        staff = Staff(tenant_id=tid, name='Private specialist')
        connection = PaymentConnection(tenant_id=tid, label='Private merchant', mode='test',
                                       key_last4='1234', webhook_secret_enc=b'unused')
        channel = Channel(tenant_id=tid, kind='whatsapp', external_id='events-test-phone')
        db.add_all([service, staff, connection, channel])
        db.flush()
        slot = Slot(tenant_id=tid, service_id=service.id, staff_id=staff.id,
                    starts_at=future, ends_at=future + timedelta(minutes=30))
        db.add(slot)
        db.flush()
        booking = Booking(tenant_id=tid, conversation_id=subscribed, slot_id=slot.id,
            customer_name='Private customer', customer_phone='+919999000001', expires_at=future, reminder_at=future)
        payment = PaymentRequest(tenant_id=tid, connection_id=connection.id, conversation_id=subscribed,
            approval_key=uid(), amount=10000, description='Private deposit',
            customer_name='Private customer', customer_phone='+919999000001', expires_at=future)
        campaign = Campaign(tenant_id=tid, channel_id=channel.id, name='Private campaign',
                            state='scheduled', snapshot={}, bindings=[])
        contact = Contact(tenant_id=tid, channel='whatsapp', external_user='919999000001')
        db.add_all([booking, payment, campaign, contact])
        db.flush()
        # Held appointments and unverified/unpaid links do not emit completion events.
        booking.state, payment.state = 'confirmed', 'paid'
        db.add(CampaignRecipient(tenant_id=tid, campaign_id=campaign.id, contact_id=contact.id,
                                 is_test=False, values=[], state='sent'))
        db.flush()
        booking.state, payment.state = 'confirmed', 'paid'
    campaign_delivery.maintain()
    campaign_delivery.maintain()
    events = payloads(tenant)
    assert sorted(p['type'] for p in events) == sorted([
        'booking.created', 'payment.completed', 'message.sent', 'campaign.completed'])
    assert 'Private' not in json.dumps(events) and '919999000001' not in json.dumps(events)


def test_source_rollback_and_cross_tenant_fanout(client, tenant, endpoint, subscribed):
    with pytest.raises(RuntimeError), Session.begin() as db:
        db.add(Message(conversation_id=subscribed, role='user', content='Rollback text'))
        db.flush()
        raise RuntimeError('source rollback')
    assert payloads(tenant) == []
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    second = client.post(fixtures.BASE, headers={'X-Admin-Key': other['admin_key']}, json={
        **fixtures.BODY, 'events': sorted(EVENTS)}).json()
    client.patch(fixtures.BASE + '/' + second['id'], headers={'X-Admin-Key': other['admin_key']},
                 json={'enabled': True, 'confirm_sharing': True})
    with Session.begin() as db:
        db.add(Message(conversation_id=subscribed, role='user', content='Scoped text'))
    assert len(payloads(tenant)) == 1
    with Session() as db:
        assert list(db.scalars(select(WebhookDelivery).where(WebhookDelivery.tenant_id == other['tenant_id']))) == []
