import asyncio
import io
import json
import logging
import time
from decimal import Decimal

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from sentry_sdk.transport import Transport

from app import telemetry
from app.config import settings
from app.db import Conversation, Message, Session, UsageEvent, uid
from app.observability import JsonFormatter, bind, correlation, integration, observed_job, scrub_event

PRIVATE = 'private customer message 15551234567 secret-api-token'


def test_log_privacy_correlation_and_concurrent_contexts():
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger('app.privacy_test')
    logger.addHandler(handler)
    tenant_id, conversation_id = uid(), uid()

    async def task(request_id):
        with correlation(request_id=request_id):
            bind(tenant_id=tenant_id, conversation_id=conversation_id)
            await asyncio.sleep(0)
            try:
                raise RuntimeError(PRIVATE)
            except RuntimeError:
                logger.exception('Provider failed: %s', PRIVATE)
            logger.info({'message': PRIVATE})

    async def run():
        await asyncio.gather(task(uid()), task(uid()))
    try:
        asyncio.run(run())
    finally:
        logger.removeHandler(handler)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len({row['request_id'] for row in rows}) == 2
    assert all(row['tenant_id'] == tenant_id and row['conversation_id'] == conversation_id for row in rows)
    assert all(value not in output.getvalue() for value in PRIVATE.split())


def test_sentry_envelope_scrubs_context_bodies_locals_and_attachments():
    sent = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            sent.append(envelope)

    client = sentry_sdk.Client(dsn='https://public@errors.example/1', transport=MemoryTransport,
                               default_integrations=False, before_send=scrub_event, send_default_pii=False,
                               include_local_variables=False, max_breadcrumbs=0,
                               enable_logs=False, enable_metrics=False)
    with sentry_sdk.isolation_scope() as scope, correlation(request_id=uid(), tenant_id=uid()):
        scope.set_client(client)
        scope.set_user({'email': PRIVATE})
        scope.set_extra('body', PRIVATE)
        scope.add_attachment(bytes=PRIVATE.encode(), filename='private.txt')
        try:
            raise RuntimeError(PRIVATE)
        except RuntimeError:
            event_id = sentry_sdk.capture_exception()
        client.flush()
    assert sent and len(sent[0].items) == 1
    data = sent[0].items[0].payload.json
    assert data['event_id'] == event_id and data['tags']['request_id']
    encoded = json.dumps(data)
    assert all(value not in encoded for value in PRIVATE.split())
    assert all(field not in data for field in ('request', 'user', 'extra', 'breadcrumbs'))
    client.close()


def test_metrics_auth_queues_shared_integrations_cost_and_privacy(client, tenant, monkeypatch):
    headers = {'Authorization': 'Bearer ' + 'm' * 40}
    monkeypatch.setattr(settings, 'metrics_token', None)
    assert client.get('/metrics').status_code == 404
    monkeypatch.setattr(settings, 'metrics_token', 'm' * 40)
    assert client.get('/metrics').status_code == 401
    assert client.get('/metrics', headers=tenant['headers']).status_code == 401
    assert client.get('/metrics', headers={'Authorization': 'Basic invalid'}).status_code == 422
    assert client.get('/metrics', headers={'Authorization': 'Bearer workspace-key'}).status_code == 403
    with telemetry.redis_client() as redis:
        redis.delete(telemetry.KEY)
        redis.zadd('arq:queue', {'observability-fixture': (time.time() - 90) * 1000})
    with integration('openai'):
        pass
    with pytest.raises(RuntimeError), integration('openai'):
        raise RuntimeError(PRIVATE)
    with Session.begin() as db:
        conv = Conversation(id=uid(), tenant_id=tenant['tenant_id'])
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, role='user', content=PRIVATE))
        db.add(UsageEvent(tenant_id=tenant['tenant_id'], kind='chat', cost_usd=Decimal('.125')))
    try:
        response = client.get('/metrics', headers=headers)
        assert response.status_code == 200 and 'text/plain' in response.headers['content-type']
        samples = [sample for family in text_string_to_metric_families(response.text) for sample in family.samples]
        def value(metric_name, **labels):
            return next(sample.value for sample in samples if sample.name == metric_name and sample.labels == labels)
        assert value('chatbot_queue_depth', queue='arq') >= 1
        assert value('chatbot_queue_oldest_seconds', queue='arq') >= 90
        assert value('chatbot_integration_calls_total', name='openai', outcome='error') == 1
        assert value('chatbot_integration_calls_total', name='openai', outcome='success') == 1
        assert value('chatbot_ai_cost_usd_today', tenant_id=tenant['tenant_id']) == .125
        assert value('chatbot_messages_today', tenant_id=tenant['tenant_id']) == 1
        assert PRIVATE not in response.text and tenant['admin_key'] not in response.text
    finally:
        with telemetry.redis_client() as redis:
            redis.zrem('arq:queue', 'observability-fixture')


def test_request_and_job_ids_and_redis_failure_do_not_change_outcomes(client, tenant, monkeypatch):
    response = client.get('/api/v1/tenant', headers={**tenant['headers'], 'X-Request-Id': PRIVATE})
    assert len(response.headers['X-Request-Id']) == 36 and PRIVATE not in str(response.headers)
    request_id = uid()
    assert client.get('/api/v1/tenant', headers={**tenant['headers'], 'X-Request-Id': request_id}).headers[
        'X-Request-Id'] == request_id
    def fail():
        raise RuntimeError(PRIVATE)
    client.app.add_api_route('/test/observability-error', fail)
    try:
        with TestClient(client.app, raise_server_exceptions=False) as error_client:
            failed = error_client.get('/test/observability-error', headers={'X-Request-Id': request_id})
        assert failed.status_code == 500 and failed.headers['X-Request-Id'] == request_id
        assert PRIVATE not in failed.text
    finally:
        client.app.router.routes.pop()

    @observed_job
    async def failing_job(ctx, tenant_id):
        raise RuntimeError(PRIVATE)
    with pytest.raises(RuntimeError, match='private'):
        asyncio.run(failing_job({'job_id': 'cron:fixture:15551234567'}, tenant['tenant_id']))
    from redis.exceptions import ConnectionError
    monkeypatch.setattr(telemetry, 'redis_client', lambda: (_ for _ in ()).throw(ConnectionError(PRIVATE)))
    assert client.get('/api/v1/tenant', headers=tenant['headers']).status_code == 200
