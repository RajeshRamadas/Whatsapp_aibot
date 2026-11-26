"""Meta generation, cancellation and send eligibility use only fake providers."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import rag
from app.api.meta_dm import store
from app.db import AnswerReview, Channel, Conversation, Membership, Message, Session, Tenant, engine, now, uid
from app.models.meta_bot import MetaBotTurn as Turn
from app.services import meta_bot, meta_dm
from app.services import meta_dm_replies as replies
from app.services.crypto import encrypt
from tests import test_meta_dm_connections as connections
from tests.test_api import upload
from tests.test_meta_dm import payload

admin = connections.admin


@pytest.fixture
def bot(client, admin, monkeypatch):
    tenant_id = admin['X-Workspace-Id']
    with Session.begin() as db:
        db.get(Tenant, tenant_id).bot_live = True
        channel = Channel(id=uid(), tenant_id=tenant_id, kind='instagram', external_id='654321',
                          credentials_enc=encrypt(tenant_id, 'fixture-token'))
        db.add(channel)
        channel_id = channel.id
    for module in (meta_bot, meta_dm, replies):
        monkeypatch.setattr(module, 'publish_sync', lambda *args: None)
    monkeypatch.setattr('app.api.inbox.publish_sync', lambda *args: None)
    monkeypatch.setattr('app.api.meta_dm_connections.publish_sync', lambda *args: None)
    return {'id': channel_id, 'tenant': tenant_id, 'headers': admin}


def enable(client, bot, value=True):
    response = client.put('/api/v1/meta-dm/channels/' + bot['id'] + '/bot', headers=bot['headers'],
                          json={'enabled': value})
    assert response.status_code == 200, response.text
    return response


def incoming(bot, text='refund policy', stamp=None):
    data = payload('instagram', uid(), '654321')
    item = data['entry'][0]['messaging'][0]
    item['timestamp'] = int((stamp or now()).timestamp() * 1000)
    item['message']['text'] = text
    assert store(data) == 1 and meta_dm.process_one()
    with Session() as db:
        conv = db.scalar(select(Conversation).where(Conversation.tenant_id == bot['tenant']))
        return conv.id


def turns():
    with Session() as db:
        return db.scalars(select(Turn).order_by(Turn.incoming_message_id)).all()


def outgoing(conv):
    with Session() as db:
        return db.scalars(select(Message).where(Message.conversation_id == conv, Message.role == 'assistant')).all()


def answer(monkeypatch, fn=None):
    def generate(*args, **kwargs):
        assert engine.pool.checkedout() == 0 and kwargs['knowledge_only'] and kwargs['history_before']
        if fn:
            fn()
        return rag.Answer('Refunds within 30 days. [1]', [{'number': 1, 'filename': 'policy.txt'}], '', grounded=True)
    monkeypatch.setattr(rag, 'answer', generate)


@pytest.mark.parametrize('kind', ['instagram', 'messenger'])
def test_grounded_turn_is_durable_and_uses_guarded_delivery(client, bot, monkeypatch, kind):
    with Session.begin() as db:
        db.get(Channel, bot['id']).kind = kind
    enable(client, bot)
    data = payload('page' if kind == 'messenger' else 'instagram', uid(), '654321')
    data['entry'][0]['messaging'][0]['timestamp'] = int(now().timestamp() * 1000)
    assert store(data) == 1 and meta_dm.process_one()
    turn = turns()[0]
    answer(monkeypatch)
    assert meta_bot.process_one() and not meta_bot.process_one()
    messages = outgoing(turn.conversation_id)
    assert len(messages) == 1 and messages[0].delivery_status == 'queued' and messages[0].sources
    sent = []
    def send(*args):
        assert engine.pool.checkedout() == 0
        sent.append(args)
        return 'meta-bot-sent'
    monkeypatch.setattr(replies, 'send', send)
    assert replies.dispatch_one() and not replies.dispatch_one() and len(sent) == 1
    assert outgoing(turn.conversation_id)[0].delivery_status == 'sent'
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(AnswerReview)) == 1


def test_real_knowledge_pipeline_runs_with_fakes_and_records_citations(client, bot):
    workspace = {'headers': bot['headers']}
    upload(client, workspace, 'policy.txt', 'Refunds within 30 days.')
    enable(client, bot)
    conv = incoming(bot)
    assert meta_bot.process_one()
    message = outgoing(conv)[0]
    assert message.sources and 'policy.txt' in message.content


@pytest.mark.parametrize('blocked', ['default', 'paused', 'suspended', 'old'])
def test_disabled_ineligible_accounts_never_generate(client, bot, monkeypatch, blocked):
    if blocked != 'default':
        enable(client, bot)
    with Session.begin() as db:
        tenant = db.get(Tenant, bot['tenant'])
        if blocked == 'paused':
            tenant.bot_live = False
        if blocked == 'suspended':
            tenant.suspended = True
    conv = incoming(bot, stamp=now() - timedelta(hours=25) if blocked == 'old' else None)
    monkeypatch.setattr(rag, 'answer', lambda *args, **kwargs: pytest.fail('Unexpected model call'))
    assert not meta_bot.process_one() and not turns() and not outgoing(conv)


@pytest.mark.parametrize('race', ['takeover', 'close', 'disable', 'newer', 'expiry', 'suspend', 'pause',
                                  'takeover_handback'])
def test_draft_cannot_survive_changed_ownership_or_eligibility(client, bot, monkeypatch, race):
    enable(client, bot)
    conv = incoming(bot)
    path = '/api/v1/inbox/conversations/' + conv
    def changed():
        if race in ('takeover', 'close', 'takeover_handback'):
            assert client.post(path + ('/close' if race == 'close' else '/take-over'),
                               headers=bot['headers']).status_code == 200
            if race == 'takeover_handback':
                assert client.post(path + '/hand-back', headers=bot['headers']).status_code == 200
        elif race == 'disable':
            enable(client, bot, False)
        elif race == 'newer':
            incoming(bot, 'another question')
        else:
            with Session.begin() as db:
                if race == 'expiry':
                    db.get(Conversation, conv).last_inbound_at = now() - timedelta(hours=25)
                elif race == 'pause':
                    db.get(Tenant, bot['tenant']).bot_live = False
                else:
                    db.get(Tenant, bot['tenant']).suspended = True
    answer(monkeypatch, changed)
    assert meta_bot.process_one() and not outgoing(conv)
    assert turns()[0].status == 'cancelled'
    if race in ('expiry', 'pause', 'suspend'):
        with Session() as db:
            assert db.get(Conversation, conv).status == 'waiting'


@pytest.mark.parametrize('reason', ['agent', 'closed', 'quota', 'rate', 'ungrounded', 'too_long', 'failure'])
def test_handoff_paths_do_not_publish_unverified_facts(client, bot, monkeypatch, reason):
    enable(client, bot)
    conv = incoming(bot, 'agent' if reason == 'agent' else 'refund policy')
    answer(monkeypatch)
    if reason == 'closed':
        with Session.begin() as db:
            tenant = db.get(Tenant, bot['tenant'])
            tenant.timezone = 'UTC'
            tenant.business_hours = json.dumps({str((now().weekday() + 1) % 7): ['10:00', '11:00']})
    if reason == 'quota':
        monkeypatch.setattr(meta_bot, 'reserve', lambda *args: False)
    if reason == 'rate':
        monkeypatch.setattr(rag.chat_limiter, 'allow', lambda *args: False)
    if reason in ('agent', 'closed', 'quota', 'rate'):
        monkeypatch.setattr(rag, 'answer', lambda *args, **kwargs: pytest.fail('Unexpected paid model call'))
    if reason in ('ungrounded', 'too_long'):
        result = rag.Answer('ह' * 350 if reason == 'too_long' else 'Unverified draft', [], '',
                            grounded=reason == 'too_long', uncertain=reason == 'ungrounded')
        monkeypatch.setattr(rag, 'answer', lambda *args, **kwargs: result)
    if reason == 'failure':
        monkeypatch.setattr(rag, 'answer', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('private-body')))
    assert meta_bot.process_one()
    message = outgoing(conv)[0]
    assert message.reply_key.startswith('meta-notice:') and len(message.content.encode()) <= 1000
    assert not message.sources and 'Unverified draft' not in message.content and 'private-body' not in message.content
    with Session() as db:
        assert db.get(Conversation, conv).status == 'waiting'
    monkeypatch.setattr(replies, 'send', lambda *args: 'notice-sent')
    assert replies.dispatch_one() and outgoing(conv)[0].delivery_status == 'sent'


def test_superseded_and_stale_generation_are_never_replayed(client, bot, monkeypatch):
    enable(client, bot)
    conv = incoming(bot)
    incoming(bot, 'newer question')
    answer(monkeypatch)
    assert turns()[0].status == 'cancelled'
    with Session.begin() as db:
        row = db.get(Turn, turns()[1].id)
        row.status, row.claimed_at, row.claim_token = 'processing', now() - timedelta(minutes=11), uid()
    meta_bot.expire_stale()
    assert not meta_bot.process_one() and turns()[1].status == 'failed'
    assert outgoing(conv)[0].reply_key.startswith('meta-notice:')


def test_setting_auth_isolation_validation_and_busy_reply(client, bot, tenant):
    path = '/api/v1/meta-dm/channels/' + bot['id'] + '/bot'
    assert client.put(path, headers=tenant['headers'], json={'enabled': True}).status_code == 403
    assert client.put(path, headers={**bot['headers'], 'X-Workspace-Id': tenant['tenant_id']},
                      json={'enabled': True}).status_code in (403, 404)
    assert client.put('/api/v1/meta-dm/channels/' + uid() + '/bot', headers=bot['headers'],
                      json={'enabled': True}).status_code == 404
    assert client.put(path.replace(bot['id'], 'invalid'), headers=bot['headers'],
                      json={'enabled': True}).status_code == 422
    for body in [{'enabled':'yes'}, {'enabled':True, 'tenant_id':tenant['tenant_id']}]:
        assert client.put(path, headers=bot['headers'], json=body).status_code == 422
    enable(client, bot)
    conv = incoming(bot)
    with Session.begin() as db:
        db.add(Message(conversation_id=conv, role='assistant', direction='outbound', content='Sending',
                       delivery_status='sending', dispatch_started_at=now()))
    assert client.put(path, headers=bot['headers'], json={'enabled': False}).status_code == 409
    with Session.begin() as db:
        db.scalar(select(Membership).where(Membership.tenant_id == bot['tenant'])).role = 'viewer'
    assert client.put(path, headers=bot['headers'], json={'enabled': True}).status_code == 403
    client.cookies.clear()
    assert client.put(path, json={'enabled':True}).status_code == 401


def test_foreign_workspace_and_queued_reply_cancellation(client, bot, monkeypatch):
    other = client.post('/api/v1/workspaces', headers=bot['headers'], json={'name': 'Other workspace'})
    assert other.status_code == 201
    path = '/api/v1/meta-dm/channels/' + bot['id'] + '/bot'
    assert client.put(path, headers={**bot['headers'], 'X-Workspace-Id': other.json()['tenant_id']},
                      json={'enabled': True}).status_code == 404
    enable(client, bot)
    conv = incoming(bot)
    answer(monkeypatch)
    assert meta_bot.process_one() and outgoing(conv)[0].delivery_status == 'queued'
    enable(client, bot, False)
    assert outgoing(conv)[0].delivery_status == 'failed' and not replies.dispatch_one()
    enable(client, bot)
    assert not meta_bot.process_one() and not replies.dispatch_one()
