import asyncio
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, update

from app import whatsapp
from app.config import settings
from app.db import Campaign, Channel, Contact, Session, Template, engine, now, uid
from app.db import CampaignRecipient as Recipient
from app.jobs import worker
from app.rate_limits import RateLimiter
from app.services import campaign_delivery as delivery
from app.services import campaigns
from tests.conftest import wa_payload
from tests.test_campaigns import BASE
from tests.test_campaigns import draft as draft
from tests.test_whatsapp import post
from tests.test_whatsapp_statuses import status_payload


@pytest.fixture
def sending(client, draft, monkeypatch):
    tenant, body, campaign = draft
    monkeypatch.setattr(settings, 'campaign_sends_per_second', 80)
    RateLimiter(1, namespace='campaign-send').clear()
    calls = []

    class Meta:
        def request(self, phone, **kwargs):
            assert engine.pool.checkedout() == 0
            if phone == '123':
                assert kwargs['params']['fields'] == 'owner_business_info'
                return {'owner_business_info': {'id': '456'}}
            assert phone == 'PNID1'
            assert kwargs['params']['fields'] == 'quality_rating,whatsapp_business_manager_messaging_limit'
            return {'quality_rating': 'GREEN', 'whatsapp_business_manager_messaging_limit': 'TIER_2K'}

    def graph(phone, payload, tenant_id):
        assert engine.pool.checkedout() == 0
        calls.append(payload)
        return httpx.Response(200, json={'messages': [{'id': f'wamid.campaign.{len(calls)}'}]})

    monkeypatch.setattr(delivery, 'MetaSignup', Meta)
    monkeypatch.setattr(whatsapp, 'graph_message', graph)
    path, headers = BASE + '/' + campaign['id'], tenant['headers']
    contact_id = campaign['preview'][0]['contact_id']
    test = client.post(path + '/test', headers=headers, json={'contact_id': contact_id, 'own_number': True}).json()
    delivery.deliver(test['id'], tenant['tenant_id'])
    assert client.post(path + '/start', headers=headers, json={'confirm': True}).status_code == 200
    with Session() as db:
        audience = db.scalar(select(Recipient).where(Recipient.tenant_id == tenant['tenant_id'],
            Recipient.campaign_id == campaign['id'], Recipient.is_test.is_(False)))
    return tenant, body, campaign, audience.id, calls


def test_sending_signed_results_cost_and_stop_attribution(client, sending, sent):
    tenant, _, campaign, identity, calls = sending
    asyncio.run(worker.send_campaign_recipient({}, identity, tenant['tenant_id']))
    delivery.deliver(identity, tenant['tenant_id'])
    assert len(calls) == 2 and calls[-1]['template']['components'][0]['parameters'][0]['text'] == 'Asha'
    assert calls[-1]['biz_opaque_callback_data'] == identity
    with Session() as db:
        provider_id = db.get(Recipient, identity).provider_id
    for state, billable in [('read', True), ('sent', False), ('delivered', True), ('failed', False)]:
        payload = status_payload(state, provider_id)
        payload['entry'][0]['changes'][0]['value']['statuses'][0]['pricing'] = {'billable': billable}
        assert post(client, payload).status_code == 200
    assert post(client, status_payload('failed', provider_id, phone='OTHER')).status_code == 200
    assert client.post('/webhooks/whatsapp', json=status_payload('failed', provider_id)).status_code == 401
    assert post(client, wa_payload('STOP', sender='15550000001')).status_code == 200
    delivery.maintain()
    report = client.get(BASE + '/' + campaign['id'], headers=tenant['headers']).json()
    assert report['state'] == 'completed'
    assert all(report['results'][key] == 1 for key in ('sent', 'delivered', 'read', 'replied', 'opt_outs'))
    assert Decimal(report['estimated_billable_cost']) == Decimal('0.25')
    assert report['pricing_unconfirmed'] == 0 and report['test_accepted']


def test_opt_out_after_confirmation_and_cancel_never_send(client, sending):
    tenant, _, campaign, identity, calls = sending
    contact_id = campaign['preview'][0]['contact_id']
    client.put('/api/v1/contacts/' + contact_id + '/consent', headers=tenant['headers'],
               json={'opted_in': False, 'source': 'withdrawn'})
    delivery.deliver(identity, tenant['tenant_id'])
    assert len(calls) == 1
    with Session() as db:
        assert db.get(Recipient, identity).state == 'suppressed'
    client.post(BASE + '/' + campaign['id'] + '/cancel', headers=tenant['headers'])
    delivery.deliver(identity, tenant['tenant_id'])
    assert len(calls) == 1


@pytest.mark.parametrize('field,value', [
    ('phone_number_quality_update', {'event': 'FLAGGED'}),
    ('business_capability_update', {'max_daily_conversations_per_business': 'TIER_250'}),
])
def test_quality_drop_pauses_alerts_and_requires_explicit_resume(client, sending, monkeypatch, field, value):
    tenant, _, campaign, identity, calls = sending
    alerts = []
    monkeypatch.setattr(delivery, 'publish_sync', lambda *args: alerts.append(args))
    payload = {'entry': [{'id': '123', 'changes': [{'field': field, 'value': value}]}]}
    assert post(client, payload).status_code == 200
    with Session() as db:
        row = db.get(Campaign, campaign['id'])
        assert row.state == 'paused' and row.reason == 'channel_quality'
        channel_id = row.channel_id
    assert alerts and alerts[0][1] == 'campaign.updated'
    delivery.quality(channel_id, tenant['tenant_id'], 'GREEN', 1000, verified=True)
    delivery.deliver(identity, tenant['tenant_id'])
    assert len(calls) == 1  # Recovery does not silently resume.
    client.post(BASE + '/' + campaign['id'] + '/start', headers=tenant['headers'], json={'confirm': True})
    delivery.deliver(identity, tenant['tenant_id'])
    assert len(calls) == 2


@pytest.mark.parametrize('kind,expected', [('permanent', 'failed'), ('timeout', 'unknown'), ('retry', 'queued')])
def test_failure_classes_and_bounded_retries(client, sending, monkeypatch, kind, expected):
    tenant, _, _, identity, calls = sending

    def fail(*args, **kwargs):
        assert engine.pool.checkedout() == 0
        if kind == 'timeout':
            raise httpx.ReadTimeout('private recipient body')
        status = 429 if kind == 'retry' else 400
        response = httpx.Response(status, request=httpx.Request('POST', 'https://example.invalid'),
            json={'error': {'code': 130429 if kind == 'retry' else 131026, 'message': 'private recipient body'}},
            headers={'Retry-After': '60'})
        response.raise_for_status()

    monkeypatch.setattr(whatsapp, 'graph_message', fail)
    delivery.deliver(identity, tenant['tenant_id'])
    with Session() as db:
        row = db.get(Recipient, identity)
        assert row.state == expected and row.attempts == 1 and 'private' not in row.error_code
        if kind == 'retry':
            assert row.next_attempt_at > now() + timedelta(seconds=55)
    if kind == 'retry':
        for _ in range(4):
            with Session.begin() as db:
                db.get(Recipient, identity).next_attempt_at = now() - timedelta(seconds=1)
            delivery.deliver(identity, tenant['tenant_id'])
        with Session() as db:
            assert db.get(Recipient, identity).state == 'failed' and db.get(Recipient, identity).attempts == 5
    else:
        delivery.deliver(identity, tenant['tenant_id'])
    assert len(calls) == 1


def test_early_receipt_and_abandoned_lease_are_never_resent(client, sending, monkeypatch):
    tenant, _, _, identity, _ = sending

    def early(phone, payload, tenant_id):
        delivery.receipt({'phone_number_id': phone, 'id': 'wamid.early', 'status': 'read',
                          'biz_opaque_callback_data': payload['biz_opaque_callback_data'],
                          'pricing': {'billable': True}})
        return httpx.Response(200, json={'messages': [{'id': 'wamid.early'}]})

    monkeypatch.setattr(whatsapp, 'graph_message', early)
    delivery.deliver(identity, tenant['tenant_id'])
    with Session() as db:
        assert db.get(Recipient, identity).state == 'read'
    with Session.begin() as db:
        row = db.get(Recipient, identity)
        row.state, row.claimed_at = 'sending', now() - timedelta(minutes=3)
    delivery.maintain()
    with Session() as db:
        assert db.get(Recipient, identity).state == 'unknown'
    assert not campaigns.pending()


def test_scheduler_and_daily_capacity_are_shared_per_phone(client, sending, monkeypatch):
    tenant, body, campaign, identity, calls = sending
    queued = []

    async def enqueue(name, **kwargs):
        queued.append((name, kwargs))

    monkeypatch.setattr(worker, 'enqueue', enqueue)
    asyncio.run(worker.dispatch_campaigns({}))
    assert queued[0][0] == 'send_campaign_recipient' and queued[0][1]['recipient_id'] == identity
    monkeypatch.setattr(settings, 'campaign_daily_limit', 1)
    delivery.deliver(identity, tenant['tenant_id'])  # Same contact as the test consumes one unique-recipient slot.
    assert len(calls) == 2
    with Session.begin() as db:
        contact = db.scalar(select(Contact).where(Contact.tenant_id == tenant['tenant_id'],
                                                  Contact.external_user == '15550000002'))
        contact.opt_in_at = now()
        campaign_id, contact_id = campaign['id'], contact.id
        extra = Recipient(tenant_id=tenant['tenant_id'], campaign_id=campaign_id,
                          contact_id=contact_id, values=['Other'])
        db.add(extra)
        db.flush()
        extra_id = extra.id
    delivery.deliver(extra_id, tenant['tenant_id'])
    with Session() as db:
        assert db.get(Recipient, extra_id).error_code == 'daily_limit' and db.get(Recipient, extra_id).attempts == 0
    with Session.begin() as db:
        db.execute(update(Recipient).where(Recipient.tenant_id == tenant['tenant_id'])
                   .values(claimed_at=now() - timedelta(days=2), next_attempt_at=now() - timedelta(seconds=1)))
    delivery.deliver(extra_id, tenant['tenant_id'])
    assert len(calls) == 3


def test_rate_limit_schedule_and_fail_closed_quality(client, sending, monkeypatch):
    tenant, _, campaign, identity, calls = sending
    with Session.begin() as db:
        row = db.get(Campaign, campaign['id'])
        row.scheduled_at = now() + timedelta(hours=1)
    delivery.deliver(identity, tenant['tenant_id'])
    assert len(calls) == 1 and not campaigns.pending()
    with Session.begin() as db:
        db.get(Campaign, campaign['id']).scheduled_at = now() - timedelta(seconds=1)
    monkeypatch.setattr(settings, 'campaign_sends_per_second', 1)
    limiter = RateLimiter(1, window_seconds=1, namespace='campaign-send')
    limiter.clear()
    assert limiter.allow('PNID1')
    delivery.deliver(identity, tenant['tenant_id'])
    with Session() as db:
        row = db.get(Recipient, identity)
        assert row.state == 'queued' and row.attempts == 0 and row.error_code == 'rate_limit'
    with Session.begin() as db:
        db.get(Recipient, identity).next_attempt_at = now() - timedelta(seconds=1)
    monkeypatch.setattr(delivery, 'refresh', lambda *args: False)
    delivery.deliver(identity, tenant['tenant_id'])
    assert len(calls) == 1


def test_foreign_phone_cannot_apply_opaque_receipt(client, sending):
    tenant, _, _, identity, _ = sending
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other phone'}).json()
    assert client.patch('/api/v1/tenant', headers={'X-Admin-Key': other['admin_key']},
                        json={'whatsapp_phone_number_id': 'PNID2'}).status_code == 200
    with Session.begin() as db:
        row = db.get(Recipient, identity)
        row.state, row.claimed_at = 'sending', now()
    payload = status_payload('read', message_id='wamid.foreign', phone='PNID2')
    payload['entry'][0]['changes'][0]['value']['statuses'][0]['biz_opaque_callback_data'] = identity
    payload['entry'][0]['changes'][0]['value']['statuses'][0]['phone_number_id'] = 'PNID1'
    assert post(client, payload).status_code == 200
    with Session() as db:
        assert db.get(Recipient, identity).state == 'sending' and db.get(Recipient, identity).provider_id is None


def test_portfolio_budget_is_shared_across_workspaces_and_phone_numbers(client, sending, monkeypatch):
    _, _, _, _, calls = sending
    monkeypatch.setattr(settings, 'campaign_daily_limit', 1)
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Same portfolio'}).json()
    with Session.begin() as db:
        channel = Channel(id=uid(), tenant_id=other['tenant_id'], kind='whatsapp', external_id='PNID2',
            waba_id='789', campaign_portfolio='456', campaign_quality='GREEN', campaign_limit=2000,
            campaign_checked_at=now())
        contact = Contact(id=uid(), tenant_id=other['tenant_id'], channel='whatsapp', external_user='15550000003',
                          opt_in_at=now())
        db.add_all([channel, contact])
        db.flush()
        template = Template(id=uid(), channel_id=channel.id, meta_id='other-template', name='offer',
                            language='en', category='MARKETING', status='APPROVED', body='Hello {{1}}')
        db.add(template)
        db.flush()
        row = Campaign(id=uid(), tenant_id=other['tenant_id'], channel_id=channel.id, template_id=template.id,
            name='Shared capacity', state='scheduled', scheduled_at=now(), bindings=[],
            snapshot={'name': 'offer', 'language': 'en', 'body': 'Hello {{1}}', 'phone_id': 'PNID2'})
        db.add(row)
        db.flush()
        recipient = Recipient(id=uid(), tenant_id=other['tenant_id'], campaign_id=row.id,
                              contact_id=contact.id, values=['Other'])
        db.add(recipient)
        identity = recipient.id
    delivery.deliver(identity, other['tenant_id'])
    with Session() as db:
        assert db.get(Recipient, identity).error_code == 'daily_limit'
    assert len(calls) == 1  # The first workspace's accepted test reserved the portfolio's only slot.


def test_unknown_meta_tier_fails_closed(client, sending, monkeypatch):
    tenant, _, campaign, identity, calls = sending
    with Session.begin() as db:
        channel_id = db.get(Campaign, campaign['id']).channel_id
        db.get(Channel, channel_id).campaign_checked_at = None

    class Meta:
        def request(self, phone, **kwargs):
            return ({'owner_business_info': {'id': '456'}} if phone == '123' else
                    {'quality_rating': 'GREEN', 'whatsapp_business_manager_messaging_limit': 'UNKNOWN_TIER'})

    monkeypatch.setattr(delivery, 'MetaSignup', Meta)
    delivery.deliver(identity, tenant['tenant_id'])
    with Session() as db:
        assert db.get(Campaign, campaign['id']).state == 'paused'
        assert db.get(Recipient, identity).attempts == 0
    assert len(calls) == 1
