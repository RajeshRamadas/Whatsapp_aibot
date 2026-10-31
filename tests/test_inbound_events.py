"""Durable inbound queue tests: deduplication, debounce and exhausted retries."""
import asyncio

from arq import Retry
from sqlalchemy import select

from app import whatsapp
from app.api import whatsapp as whatsapp_api
from app.db import Conversation, InboundEvent, Message, Session
from app.jobs import worker
from tests.conftest import signed, wa_payload


def test_webhook_only_persists_and_queues(client, tenant, monkeypatch, sent):
    client.patch('/api/v1/tenant', headers=tenant['headers'],
                 json={'whatsapp_phone_number_id': 'PNID1'})
    jobs = []

    async def capture(name, **kwargs):
        jobs.append((name, kwargs))
        return kwargs['_job_id']

    monkeypatch.setattr(whatsapp_api, 'enqueue', capture)
    raw, headers = signed(wa_payload())
    assert client.post('/webhooks/whatsapp', content=raw, headers=headers).status_code == 200
    assert client.post('/webhooks/whatsapp', content=raw, headers=headers).status_code == 200
    assert len(jobs) == 2 and all(name == 'handle_message' for name, _ in jobs)
    assert jobs[0][1]['event_id'] == jobs[1][1]['event_id']
    assert sent == []
    with Session() as db:
        assert db.scalar(select(InboundEvent)).status == 'queued'


def test_quick_messages_merge_into_one_reply(client, tenant, monkeypatch, sent):
    client.patch('/api/v1/tenant', headers=tenant['headers'],
                 json={'whatsapp_phone_number_id': 'PNID1'})
    async def no_delay(_):
        return None
    monkeypatch.setattr(worker.asyncio, 'sleep', no_delay)
    a = whatsapp.accept(whatsapp.parse_messages(wa_payload('first'))[0])
    b = whatsapp.accept(whatsapp.parse_messages(wa_payload('second', msg_id='wamid.2'))[0])
    asyncio.run(worker.handle_message({'job_try': 1}, a))
    asyncio.run(worker.handle_message({'job_try': 1}, b))
    assert len(sent) == 1
    assert 'first' in sent[0][2] and 'second' in sent[0][2]
    with Session() as db:
        assert {e.status for e in db.scalars(select(InboundEvent))} == {'processed', 'merged'}


def test_provider_failures_handoff_after_five_attempts(client, tenant, monkeypatch):
    client.patch('/api/v1/tenant', headers=tenant['headers'],
                 json={'whatsapp_phone_number_id': 'PNID1'})
    async def no_delay(_):
        return None
    monkeypatch.setattr(worker.asyncio, 'sleep', no_delay)
    event_id = whatsapp.accept(whatsapp.parse_messages(wa_payload('hello'))[0])
    calls = []

    def failing_handle(*args):
        calls.append(args)
        raise RuntimeError('provider unavailable')

    monkeypatch.setattr(whatsapp, 'handle', failing_handle)
    monkeypatch.setattr(whatsapp, 'send_text', lambda *args: None)
    for attempt in range(1, 5):
        try:
            asyncio.run(worker.handle_message({'job_try': attempt}, event_id))
        except Retry:
            pass
        else:
            raise AssertionError('Expected retry')
    asyncio.run(worker.handle_message({'job_try': 5}, event_id))
    assert len(calls) == 5
    with Session() as db:
        event = db.get(InboundEvent, event_id)
        assert event.status == 'failed' and event.attempts == 5
        conv = db.scalar(select(Conversation).where(Conversation.tenant_id == tenant['tenant_id']))
        assert conv.handoff
        assert db.scalar(select(Message).where(Message.conversation_id == conv.id,
                                               Message.role == 'agent')) is not None
