import asyncio
from datetime import timedelta

import httpx
import pytest
from arq.worker import create_worker
from sqlalchemy import select

from app import whatsapp
from app.db import ApiKey, Contact, Conversation, Lead, Message, Session, Template, engine, now
from app.jobs import queue, worker
from app.models.lead_follow_ups import FOLLOW_UP_REPLY_KEY, LeadFollowUpRule
from app.services import campaign_delivery, lead_follow_ups
from app.services.channels import whatsapp_channel
from tests.conftest import wa_payload
from tests.test_whatsapp import link_number, post

PATH = '/api/v1/lead-follow-up'
PHONE = '15551234567'


@pytest.fixture
def follow_up(client, tenant, monkeypatch):
    link_number(client, tenant)
    tenant_id = tenant['tenant_id']
    with Session.begin() as db:
        channel = whatsapp_channel(db, tenant_id)
        channel.waba_id, channel.campaign_portfolio = '123', '456'
        channel.campaign_quality, channel.campaign_limit, channel.campaign_checked_at = 'GREEN', 1000, now()
        template = Template(channel_id=channel.id, meta_id='template-1', name='salon_follow_up', language='en',
            category='MARKETING', status='APPROVED', body='Need help booking at {{1}}?')
        db.add(template)
        db.flush()
        template_id = template.id
    conv = whatsapp.conversation_for(tenant_id, PHONE)
    with Session.begin() as db:
        created = now() - timedelta(hours=4)
        db.get(Conversation, conv.id).last_inbound_at = created
        db.get(Contact, conv.contact_id).opt_in_at = created
        db.add(Lead(tenant_id=tenant_id, conversation_id=conv.id, created_at=created))
    calls = []

    def graph(phone, payload, tenant_id):
        assert engine.pool.checkedout() == 0
        calls.append((phone, payload, tenant_id))
        return httpx.Response(200, json={'messages': [{'id': 'wamid.follow-up'}]})

    monkeypatch.setattr(whatsapp, 'graph_message', graph)
    return tenant, conv, {'enabled': True, 'delay_hours': 2, 'template_id': template_id, 'values': ['Beautiva']}, calls


def configure(client, fixture, *, due=True):
    tenant, _, body, calls = fixture
    r = client.put(PATH, headers=tenant['headers'], json=body)
    assert r.status_code == 200 and r.json() == {**body, 'template_available': True}, r.text
    assert calls == []
    if due:
        with Session.begin() as db:
            rule = db.get(LeadFollowUpRule, tenant['tenant_id'])
            rule.configured_at = rule.next_check_at = now() - timedelta(hours=3)


def claim_message(db, conversation_id):
    return db.scalar(select(Message).where(Message.conversation_id == conversation_id,
                                          Message.reply_key == FOLLOW_UP_REPLY_KEY))


def test_policy_auth_scope_validation_and_pause(client, follow_up):
    tenant, _, body, _ = follow_up
    headers = tenant['headers']
    assert client.get(PATH).status_code == client.put(PATH, json=body).status_code == 401
    assert client.get(PATH, headers=headers).json()['enabled'] is False
    assert client.get(PATH, headers={**headers, 'X-Workspace-Id': 'foreign'}).status_code == 404
    assert client.put(PATH, headers=headers, json={**body, 'template_id': 'foreign'}).status_code == 404
    for invalid in ({'delay_hours': 0}, {'values': []}, {'template_id': None}, {'delay_hours': '2'}):
        assert client.put(PATH, headers=headers, json={**body, **invalid}).status_code == 422
    configure(client, follow_up, due=False)
    with Session.begin() as db:
        stamp = db.get(LeadFollowUpRule, tenant['tenant_id']).configured_at
    assert client.put(PATH, headers=headers, json=body).status_code == 200
    with Session.begin() as db:
        assert db.get(LeadFollowUpRule, tenant['tenant_id']).configured_at == stamp
        db.get(Template, body['template_id']).status = 'PAUSED'
    paused = client.put(PATH, headers=headers, json={'enabled': False})
    assert paused.json() == {**body, 'enabled': False, 'template_available': False}
    with Session.begin() as db:
        db.scalar(select(ApiKey).where(ApiKey.tenant_id == tenant['tenant_id'])).scopes = ['viewer']
    assert client.get(PATH, headers=headers).status_code == 403
    assert client.put(PATH, headers=headers, json=body).status_code == 403


def test_due_follow_up_uses_regular_queue_and_never_sends_twice(client, follow_up):
    tenant, conv, _, calls = follow_up
    configure(client, follow_up, due=False)
    assert lead_follow_ups.pending() == []  # Existing leads wait the full delay after activation.
    configure(client, follow_up)

    async def send_queued():
        await worker.dispatch_lead_follow_ups({})
        run = create_worker(worker.QueueWorkerSettings, burst=True, max_burst_jobs=1, handle_signals=False)
        try:
            await asyncio.wait_for(run.async_run(), timeout=10)
            assert run.jobs_complete == 1 and run.jobs_failed == 0
        finally:
            await run.close()
            await queue.close_pool()

    asyncio.run(send_queued())
    lead_follow_ups.deliver(tenant['tenant_id'])
    assert lead_follow_ups.pending() == [] and len(calls) == 1
    phone, payload, tenant_id = calls[0]
    assert (phone, payload['to'], payload['type'], tenant_id) == ('PNID1', PHONE, 'template', tenant['tenant_id'])
    assert payload['template']['components'][0]['parameters'][0]['text'] == 'Beautiva'
    with Session.begin() as db:
        message = claim_message(db, conv.id)
        assert (message.content, message.delivery_status, message.channel_message_id) == (
            'Need help booking at Beautiva?', 'sent', 'wamid.follow-up')
        channel = whatsapp_channel(db, tenant_id)
        channel.campaign_limit = 1
        db.flush()
        assert not campaign_delivery.capacity_available(db, channel, '15550000001')
        assert campaign_delivery.capacity_available(db, channel, PHONE)


@pytest.mark.parametrize('change', ['reply', 'stop', 'opt_out', 'won', 'closed', 'withdrawn_template'])
def test_follow_up_rechecks_eligibility_after_provider_preflight(client, follow_up, monkeypatch, sent, change):
    tenant, conv, body, calls = follow_up
    configure(client, follow_up)

    def refresh(channel_id, tenant_id):
        if change in ('reply', 'stop'):
            assert post(client, wa_payload('STOP' if change == 'stop' else 'Can you call me?')).status_code == 200
        else:
            with Session.begin() as db:
                if change == 'opt_out':
                    db.get(Contact, conv.contact_id).opt_out_at = now()
                elif change == 'won':
                    db.get(Lead, conv.id).status = 'won'
                elif change == 'closed':
                    db.get(Conversation, conv.id).status = 'closed'
                else:
                    db.get(Template, body['template_id']).status = 'PAUSED'
        return True

    monkeypatch.setattr(campaign_delivery, 'refresh', refresh)
    lead_follow_ups.deliver(tenant['tenant_id'])
    assert calls == []
    with Session() as db:
        assert claim_message(db, conv.id) is None


def test_ambiguous_send_and_signed_receipt_recover_without_resend(client, follow_up, monkeypatch):
    tenant, conv, _, calls = follow_up
    configure(client, follow_up)

    def timeout(phone, payload, tenant_id):
        calls.append(payload)
        raise httpx.ReadTimeout('simulated timeout')

    monkeypatch.setattr(whatsapp, 'graph_message', timeout)
    lead_follow_ups.deliver(tenant['tenant_id'])
    with Session.begin() as db:
        message = claim_message(db, conv.id)
        assert message.delivery_status == 'unknown'
        message.delivery_status = 'sending'  # Simulate a worker lost after committing its claim.
        message.dispatch_started_at = now() - timedelta(minutes=3)
    assert lead_follow_ups.pending() == []
    lead_follow_ups.deliver(tenant['tenant_id'])
    assert len(calls) == 1
    event = {'phone_number_id': 'WRONG_CHANNEL', 'id': 'wamid.follow-up', 'status': 'read',
             'biz_opaque_callback_data': calls[0]['biz_opaque_callback_data']}
    assert not whatsapp.record_status(event)
    with Session() as db:
        assert claim_message(db, conv.id).delivery_status == 'unknown'
    assert whatsapp.record_status({**event, 'phone_number_id': 'PNID1'})
    with Session() as db:
        assert claim_message(db, conv.id).delivery_status == 'read'
