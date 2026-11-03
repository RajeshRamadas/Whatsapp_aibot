from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.config import settings
from app.db import Plan, Session, UsageDaily, UsageEvent, now
from app.services import email, usage
from app.services.widget_turns import process_turn
from tests.conftest import wa_payload
from tests.test_whatsapp import link_number, post
from tests.test_workspaces import Recorder, login


def limit(value):
    with Session.begin() as db:
        db.get(Plan, 'starter').limits = {'ai_messages': value}


def test_reservations_overage_alerts_and_reconciliation(client, tenant, monkeypatch):
    limit(5)
    tenant_id = tenant['tenant_id']
    for index in range(5):
        assert usage.reserve(tenant_id, str(index))
    assert usage.reserve(tenant_id, '0')
    assert not usage.reserve(tenant_id, 'blocked')
    response = client.put('/api/v1/usage/quota-policy', headers=tenant['headers'], json={'behavior': 'overage'})
    assert response.status_code == 200
    assert usage.reserve(tenant_id, 'extra')

    monkeypatch.setattr(settings, 'ai_prices_usd', {'openai:test': (Decimal('2'), Decimal('4'))})
    response = SimpleNamespace(model='test', usage=SimpleNamespace(prompt_tokens=100, completion_tokens=25))
    with usage.meter(tenant_id):
        assert usage.call('openai', 'chat', lambda **kw: response, model='test') is response
        usage.call('gemini', 'embedding', lambda **kw: SimpleNamespace(), model='test')
    key = usage.cache_key(tenant_id, usage.month_start(now()))
    with usage.redis_client() as redis:
        redis.set(key, 400)
    usage.reconcile()
    usage.reconcile()
    with usage.redis_client() as redis:
        assert redis.get(key) == b'6'
    with Session() as db:
        rows = db.scalars(select(UsageEvent).where(UsageEvent.kind.in_(('alert80', 'alert100')))).all()
        assert len(rows) == 2
        [daily] = db.scalars(select(UsageDaily)).all()
        assert (daily.ai_messages, daily.overage_messages, daily.ai_calls) == (6, 1, 2)
        assert (daily.input_tokens, daily.output_tokens, daily.cost_usd) == (100, 25, Decimal('0.0003'))
        assert daily.unpriced_calls == 1


def test_quota_pauses_widget_and_whatsapp(client, tenant, chats, sent):
    limit(1)
    tenant_id = tenant['tenant_id']
    assert usage.reserve(tenant_id, 'used')
    base = '/api/v1/chat/' + tenant['public_key'] + '/widget'
    turn = client.post(base + '/turns', json={'request_id': str(uuid4()), 'message': 'Hello'}).json()
    assert process_turn()
    assert client.get(base + '/turns/' + turn['id']).json()['answer'] == usage.QUOTA_NOTICE

    link_number(client, tenant)
    assert post(client, wa_payload('Hello')).status_code == 200
    assert sent[-1][2] == usage.QUOTA_NOTICE
    assert chats['openai'].calls == []
    conversations = client.get('/api/v1/inbox/conversations?status=waiting', headers=tenant['headers']).json()
    assert len(conversations['items']) == 2


def test_owner_alerts_are_sent_once_per_threshold(client, monkeypatch):
    monkeypatch.setattr(settings, 'auth_cookie_secure', False)
    recorder = Recorder()
    monkeypatch.setattr(email, 'sender', recorder)
    headers = login(client, 'usage-owner@example.com', recorder)
    response = client.post('/api/v1/workspaces', headers=headers, json={'name': 'Usage alerts'})
    assert response.status_code == 201
    tenant_id = response.json()['tenant_id']
    limit(5)
    recorder.sent.clear()
    for index in range(5):
        assert usage.reserve(tenant_id, str(index))
    usage.send_alerts()
    usage.send_alerts()
    assert {subject for _, subject, _ in recorder.sent} == {'AI usage reached 80%', 'AI usage reached 100%'}
    assert len(recorder.sent) == 2
