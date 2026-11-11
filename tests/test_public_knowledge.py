"""Model/storage work occurs only in durable jobs, with scoped and replay-safe results."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import llm
from app.db import QAPair, Session, Source, engine, now, uid
from app.models.public_api import ApiOperation, ApiRequest
from app.services import public_operations
from tests import test_public_resources as fixtures
from tests.conftest import FakeEmbedder
from tests.test_public_resources import ROOT, create

api = fixtures.api


@pytest.mark.parametrize(('path', 'body'), [('sources', {'filename': 'policy.txt', 'content': 'Refund policy'}),
                                          ('qa-pairs', {'question': 'Refund?', 'answer': 'Refund in 7 days'})])
def test_knowledge_job_and_replay(client, tenant, api, monkeypatch, path, body):
    calls = []
    class Embedder(FakeEmbedder):
        def embed(self, texts, **kwargs):
            assert engine.pool.checkedout() == 0
            calls.append(texts)
            return super().embed(texts, **kwargs)
    monkeypatch.setattr(llm, 'embedder', lambda: Embedder())
    first = create(client, api, path, body, 'knowledge')
    assert first.status_code == 202 and calls == []
    operation = first.json()
    assert create(client, api, path, body, 'knowledge').json() == operation
    assert public_operations.process_one()
    status = client.get(ROOT + '/operations/' + operation['id'], headers=api['headers'])
    assert status.json()['state'] == 'complete' and calls
    detail = client.get(ROOT + '/' + path + '/' + operation['resource_id'], headers=api['headers'])
    assert detail.status_code == 200, detail.text
    assert 'storage:' not in detail.text and 'embedding' not in detail.text
    assert not public_operations.process_one()
    with Session() as db:
        op = db.get(ApiOperation, operation['id'])
        assert op.payload == {} and op.attempts == 1
    if path == 'qa-pairs':
        url = 'qa-pairs/' + operation['resource_id'] + '/updates'
        update = create(client, api, url, {**body, 'answer': 'Updated answer'})
        assert update.status_code == 202
        assert client.delete(ROOT + '/qa-pairs/' + operation['resource_id'],
                             headers=api['headers']).status_code == 409
        assert public_operations.process_one()
        assert client.get(ROOT + '/qa-pairs/' + operation['resource_id'],
                          headers=api['headers']).json()['answer'] == 'Updated answer'
    assert client.delete(ROOT + '/' + path + '/' + operation['resource_id'],
                         headers=api['headers']).status_code == 204
    if path == 'sources':
        assert public_operations.process_one()


@pytest.mark.parametrize(('path', 'body'), [('sources', {'filename': 'policy.md', 'content': 'Policy'}),
                                          ('qa-pairs', {'question': 'Refund?', 'answer': 'Yes'})])
def test_knowledge_boundary(client, api, path, body):
    assert client.post(ROOT + '/' + path, json=body, headers={'Idempotency-Key': uid()}).status_code == 401
    assert client.post(ROOT + '/' + path, json=body,
                       headers={**api['limited'], 'Idempotency-Key': uid()}).status_code == 403
    assert create(client, api, path, {}).status_code == 422
    assert client.post(ROOT + '/' + path, json=body, headers=api['headers']).status_code == 422
    for method in ('delete',):
        url = ROOT + '/' + path + '/' + api['foreign'][path]
        assert getattr(client, method)(url).status_code == 401
        assert getattr(client, method)(url, headers=api['limited']).status_code == 403
        assert getattr(client, method)(url, headers=api['headers']).status_code == 404
        assert getattr(client, method)(ROOT + '/' + path + '/invalid', headers=api['headers']).status_code == 422
    if path == 'qa-pairs':
        url = 'qa-pairs/' + api['foreign']['qa-pairs'] + '/updates'
        assert create(client, api, url, body).status_code == 404
        assert client.post(ROOT + '/' + url, json=body, headers={'Idempotency-Key': uid()}).status_code == 401
        assert client.post(ROOT + '/' + url, json=body,
                           headers={**api['limited'], 'Idempotency-Key': uid()}).status_code == 403
        assert create(client, api, 'qa-pairs/invalid/updates', body).status_code == 422


def test_operation_boundary_and_retry(client, tenant, api, monkeypatch):
    operation = create(client, api, 'qa-pairs', {'question': 'Refund?', 'answer': 'Yes'}).json()
    url = ROOT + '/operations/' + operation['id']
    assert client.get(url).status_code == 401
    assert client.get(url, headers=api['limited']).status_code == 403
    assert client.get(ROOT + '/operations/' + uid(), headers=api['headers']).status_code == 404
    assert client.get(ROOT + '/operations/invalid', headers=api['headers']).status_code == 422
    def unavailable():
        raise RuntimeError('provider secret or customer text must never escape')
    monkeypatch.setattr(llm, 'embedder', unavailable)
    for _index in range(3):
        assert public_operations.process_one()
        with Session.begin() as db:
            db.get(ApiOperation, operation['id']).next_attempt_at = now() - timedelta(seconds=1)
    status = client.get(url, headers=api['headers']).json()
    assert status['state'] == 'failed' and status['last_error'] == 'knowledge_processing_failed'
    assert not public_operations.process_one()
    with Session.begin() as db:
        assert db.get(ApiOperation, operation['id']).payload == {}
        db.get(ApiOperation, operation['id']).created_at = now() - timedelta(days=31)
        db.scalar(select(ApiRequest)).expires_at = now() - timedelta(seconds=1)
    public_operations.expire()
    assert client.get(url, headers=api['headers']).status_code == 404


def test_concurrent_post_single_operation(client, tenant, api):
    def submit(_):
        return create(client, api, 'qa-pairs', {'question': 'Refund?', 'answer': 'Yes'}, 'race')
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(submit, range(2)))
    assert all(r.status_code == 202 for r in replies)
    assert replies[0].json() == replies[1].json()
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(ApiOperation)) == 1
        assert db.scalar(select(func.count()).select_from(QAPair)) == 1  # Foreign fixture only.
        assert db.scalar(select(func.count()).select_from(Source)) == 1
