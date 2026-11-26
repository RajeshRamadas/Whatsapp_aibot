"""Connection rollback, provider type checks and disconnect/dispatch coordination."""
import io

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.api import meta_dm_connections as api
from app.db import Channel, Conversation, Message, Session, Tenant, engine, now, uid
from app.services import meta_connections as service
from app.services import meta_dm_replies as replies
from app.services.meta_signup import MetaSignup, MetaSignupError
from tests import test_meta_dm_connections as connections

admin = connections.admin
BASE, PAYLOAD = connections.BASE, connections.PAYLOAD


@pytest.fixture
def graph(monkeypatch):
    provider = connections.FakeGraph()
    monkeypatch.setattr(api, 'MetaSignup', lambda: provider)
    return provider


@pytest.mark.parametrize('existing', [False, True])
def test_failed_save_cleans_new_subscriptions_and_preserves_existing(client, admin, monkeypatch, graph, existing):
    graph.subscribed = existing
    def fail(*args):
        raise OperationalError('synthetic save', {}, RuntimeError('private detail'))
    monkeypatch.setattr(service, 'persist_connection', fail)
    response = client.post(BASE, headers=admin, json=PAYLOAD)
    assert response.status_code == 503 and 'private detail' not in response.text
    assert graph.subscribed is existing
    assert any(call[-1] == 'DELETE' for call in graph.calls) is (not existing)


def test_cleanup_does_not_remove_a_committed_connection(client, admin, monkeypatch, graph):
    original = service.persist_connection
    def uncertain(*args):
        original(*args)
        raise OperationalError('commit acknowledgement lost', {}, RuntimeError())
    monkeypatch.setattr(service, 'persist_connection', uncertain)
    assert client.post(BASE, headers=admin, json=PAYLOAD).status_code == 503
    assert graph.subscribed and not any(call[-1] == 'DELETE' for call in graph.calls)
    with Session() as db:
        assert db.scalar(select(Channel.id).where(Channel.tenant_id == admin['X-Workspace-Id']))


def test_cleanup_failure_is_actionable_without_provider_details(client, admin, monkeypatch, graph):
    original = graph.request
    def request(*args, **kwargs):
        if kwargs.get('method') == 'DELETE':
            raise MetaSignupError('private cleanup detail')
        return original(*args, **kwargs)
    monkeypatch.setattr(graph, 'request', request)
    def fail(*args):
        raise OperationalError('save', {}, RuntimeError())
    monkeypatch.setattr(service, 'persist_connection', fail)
    response = client.post(BASE, headers=admin, json=PAYLOAD)
    assert response.status_code == 424 and 'private cleanup detail' not in response.text


def test_encryption_fails_before_any_provider_change(client, admin, monkeypatch, graph):
    def unavailable(*args):
        raise ValueError('private encryption detail')
    monkeypatch.setattr(service, 'encrypt', unavailable)
    assert client.post(BASE, headers=admin, json=PAYLOAD).status_code == 503
    assert graph.calls == []


@pytest.mark.parametrize('kind', ['instagram', 'messenger'])
def test_wrong_provider_asset_type_never_subscribes(client, admin, graph, kind):
    graph.actual_kind = 'messenger' if kind == 'instagram' else 'instagram'
    response = client.post(BASE, headers=admin, json={**PAYLOAD, 'kind': kind})
    assert response.status_code == 422
    assert not any(call[0].endswith('/subscribed_apps') for call in graph.calls)


@pytest.mark.parametrize('debug', [{'is_valid': False, 'app_id': '123'}, {'is_valid': True, 'app_id': '999'}])
def test_token_must_be_valid_for_the_configured_app(client, admin, graph, debug):
    graph.debug = debug
    assert client.post(BASE, headers=admin, json=PAYLOAD).status_code == 502
    assert [call[0] for call in graph.calls] == ['debug_token']


def queue(client, admin):
    channel = client.post(BASE, headers=admin, json=PAYLOAD).json()['id']
    with Session.begin() as db:
        conv = Conversation(id=uid(), tenant_id=admin['X-Workspace-Id'], channel='messenger',
                            meta_channel_id=channel, external_user='customer', last_inbound_at=now())
        db.add(conv)
    response = client.post('/api/v1/inbox/conversations/' + conv.id + '/reply', headers=admin,
                           json={'message': 'Staff reply'})
    assert response.status_code == 200
    return channel, response.json()['message_id']


def test_disconnect_waits_for_inflight_reply_before_succeeding(client, admin, graph, monkeypatch):
    channel, message = queue(client, admin)
    def send(*args):
        assert engine.pool.checkedout() == 0
        assert client.delete(BASE + '/' + channel, headers=admin).status_code == 409
        return 'confirmed'
    monkeypatch.setattr(replies, 'send', send)
    assert replies.dispatch_one()
    with Session() as db:
        assert db.get(Message, message).delivery_status == 'sent'
    assert client.delete(BASE + '/' + channel, headers=admin).status_code == 204
    assert not replies.dispatch_one()


def test_disconnect_tenant_lock_blocks_new_dispatch_claims(client, admin, graph):
    channel, message = queue(client, admin)
    with Session.begin() as db:
        db.get(Tenant, admin['X-Workspace-Id'], with_for_update=True)
        assert not replies.dispatch_one()
    assert client.delete(BASE + '/' + channel, headers=admin).status_code == 204
    with Session() as db:
        assert db.get(Message, message).delivery_status == 'failed'


def test_graph_supports_explicit_delete_without_tokens_in_urls():
    requests = []
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(b'{"success":true}')
    provider = MetaSignup()
    provider.opener = Opener()
    assert provider.request('123/subscribed_apps', token='private-token', method='DELETE')['success']
    assert requests[0].get_method() == 'DELETE' and 'private-token' not in requests[0].full_url


def test_parallel_enrollment_does_not_mutate_a_shared_subscription(client, admin, graph):
    from redis import Redis

    from app.config import settings
    with Redis.from_url(settings.redis_url) as redis:
        with redis.lock('locks:meta-dm-connection:' + PAYLOAD['external_id'], timeout=30):
            assert client.post(BASE, headers=admin, json=PAYLOAD).status_code == 409
    assert graph.calls == []
