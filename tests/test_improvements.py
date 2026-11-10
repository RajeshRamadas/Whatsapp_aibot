"""Recorded reply provenance, private review APIs and durable curated-answer publication."""
import hashlib
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, select

from app import rag
from app.db import AnswerReview, ApiKey, Conversation, QAPair, Session, now
from app.services import improvements
from app.services.widget_turns import process_turn
from tests.conftest import wa_payload
from tests.test_media import no_session_io as no_session_io
from tests.test_whatsapp import link_number, post

BASE = '/api/v1/improvements'
CORRECTION = {'question': 'What is the refund period?', 'answer': 'Refunds are accepted within 14 days.'}


def widget(client, tenant, question='What is the refund period?'):
    identity = str(uuid4())
    path = f'/api/v1/chat/{tenant["public_key"]}/widget/turns'
    assert client.post(path, json={'request_id': identity, 'message': question}).status_code == 202
    assert process_turn()
    with Session() as db:
        review = db.scalar(select(AnswerReview).where(AnswerReview.widget_turn_id == identity))
    return review, path + '/' + identity + '/feedback'


def test_capture_channels_votes_and_exact_question_counts(client, tenant, sent):
    headers = tenant['headers']
    first, feedback = widget(client, tenant)
    assert first.unanswered
    for vote in ('down', 'down', 'up'):
        assert client.post(feedback, json={'vote': vote}).json() == {'feedback': vote}
    second, bad = widget(client, tenant, '  WHAT is the refund period?  ')
    assert client.post(bad, json={'vote': 'down'}).status_code == 200
    assert client.post(bad, json={'vote': 'invalid'}).status_code == 422
    assert client.post(bad, headers={'Origin': 'https://foreign.example'}, json={'vote': 'up'}).status_code == 403
    legacy = '/api/v1/chat/' + tenant['public_key']
    assert client.post(legacy, json={'message': 'Shipping?'}).status_code == 200
    link_number(client, tenant)
    assert post(client, wa_payload('Hours?')).status_code == 200
    before = client.get(BASE, headers=headers).json()
    assert before['summary']['questions'] == 4
    assert before['summary']['top_questions'][0]['count'] == 2
    assert before['summary']['positive'] == before['summary']['negative'] == 1
    assert before['summary']['satisfaction'] == 50
    assert len(client.get(BASE + '?channel=whatsapp', headers=headers).json()['items']) == 1
    assert [x['id'] for x in client.get(BASE + '?reason=down', headers=headers).json()['items']] == [second.id]
    assert client.post('/api/v1/bot/test', headers=headers, json={'message': 'Preview only'}).status_code == 200
    path = '/api/v1/chat/' + tenant['public_key'] + '/widget/turns'
    client.post(path, json={'request_id': str(uuid4()), 'message': 'Human help', 'handoff': True})
    assert process_turn()
    assert client.get(BASE, headers=headers).json()['summary']['questions'] == 4


def test_permissions_validation_and_foreign_reviews(client, tenant):
    review, _ = widget(client, tenant)
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Reader', key_prefix='fixture', scopes=['viewer'],
                     key_hash=hashlib.sha256(b'review-reader').hexdigest()))
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other review workspace'}).json()
    foreign = {'X-Admin-Key': other['admin_key']}
    for method, suffix, body in [('GET', '', None), ('PATCH', '/' + review.id, {'state': 'dismissed'}),
                                 ('POST', '/' + review.id + '/answer', CORRECTION)]:
        assert client.request(method, BASE + suffix, json=body).status_code == 401
        assert client.request(method, BASE + suffix, json=body,
                              headers={'Authorization': 'Bearer review-reader'}).status_code == 403
        response = client.request(method, BASE + suffix, json=body, headers=foreign)
        assert response.status_code == (200 if method == 'GET' else 404)
        if method == 'GET':
            assert response.json()['summary']['questions'] == 0 and response.json()['items'] == []
    assert client.get(BASE, headers={**tenant['headers'], 'X-Workspace-Id': other['tenant_id']}).status_code == 404
    for query in ('?limit=0', '?offset=-1', '?reason=invalid', '?state=processing', '?channel=email',
                  '?from_date=bad', '?from_date=2025-01-01&to_date=2026-01-01',
                  '?from_date=2026-01-02&to_date=2026-01-01', '?to_date=9999-12-31'):
        assert client.get(BASE + query, headers=tenant['headers']).status_code == 422
    assert client.patch(BASE + '/bad', headers=tenant['headers'], json={'state': 'open'}).status_code == 422
    assert client.patch(BASE + '/' + review.id, headers=tenant['headers'],
                        json={'state': 'resolved'}).status_code == 422
    assert client.post(BASE + '/bad/answer', headers=tenant['headers'], json=CORRECTION).status_code == 422
    for body in [{**CORRECTION, 'question': ' '}, {**CORRECTION, 'tenant_id': other['tenant_id']}]:
        assert client.post(BASE + '/' + review.id + '/answer', headers=tenant['headers'], json=body).status_code == 422


def test_correction_worker_idempotency_retrieval_and_resolution(client, tenant, monkeypatch, no_session_io):
    review, _ = widget(client, tenant)
    headers, path = tenant['headers'], BASE + '/' + review.id
    def embed(texts, **kwargs):
        assert no_session_io()
        return [[1, 0, 0] for _ in texts]
    monkeypatch.setattr(improvements.llm, 'embedder', lambda: SimpleNamespace(embed=embed))
    assert client.patch(path, headers=headers, json={'state': 'dismissed'}).json()['state'] == 'dismissed'
    assert client.get(BASE, headers=headers).json()['total'] == 0
    assert client.patch(path, headers=headers, json={'state': 'open'}).status_code == 200
    for _ in range(2):
        assert client.post(path + '/answer', headers=headers, json=CORRECTION).status_code == 202
    assert client.patch(path, headers=headers, json={'state': 'dismissed'}).status_code == 409
    assert client.post(path + '/answer', headers=headers, json={**CORRECTION, 'answer': 'Other'}).status_code == 409
    assert improvements.process_one() and not improvements.process_one()
    assert client.post(path + '/answer', headers=headers, json=CORRECTION).json()['state'] == 'resolved'
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(QAPair)) == 1
    assert rag.answer(_tenant(tenant), None, 'refunds').sources[0]['filename'] == 'FAQ'
    assert client.patch(path, headers=headers, json={'state': 'open'}).status_code == 200
    assert client.post(path + '/answer', headers=headers,
                       json={**CORRECTION, 'answer': 'Refunds within 7 days.'}).status_code == 202
    assert improvements.process_one()
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(QAPair)) == 1
        assert db.scalar(select(QAPair.answer)) == 'Refunds within 7 days.'
    assert client.get(BASE + '?state=resolved', headers=headers).json()['total'] == 1


def _tenant(tenant):
    from app.db import Tenant
    with Session() as db:
        return db.get(Tenant, tenant['tenant_id'])


def test_worker_failure_backoff_stale_runs_and_deletion(client, tenant, monkeypatch):
    review, _ = widget(client, tenant)
    path = BASE + '/' + review.id + '/answer'
    client.post(path, headers=tenant['headers'], json=CORRECTION)
    def broken(*args):
        raise RuntimeError('secret provider details')
    monkeypatch.setattr(improvements.llm, 'embedder', broken)
    for attempt in range(5):
        assert improvements.process_one()
        with Session.begin() as db:
            row = db.get(AnswerReview, review.id)
            assert row.state == ('queued' if attempt < 4 else 'failed')
            assert 'secret' not in row.error
            assert (row.next_attempt_at - now()).total_seconds() >= 29
            if attempt < 4:
                assert not improvements.process_one()
            row.next_attempt_at = now()
    client.post(path, headers=tenant['headers'], json=CORRECTION)
    stale = improvements.claim()
    with Session.begin() as db:
        db.get(AnswerReview, review.id).claimed_at = now() - timedelta(minutes=21)
    assert improvements.claim().attempts == stale.attempts + 1
    with Session.begin() as db:
        row = db.get(AnswerReview, review.id)
        row.state, row.next_attempt_at = 'queued', now()
    def delete_during_embedding(texts):
        with Session.begin() as db:
            db.delete(db.get(Conversation, _conversation(review)))
        return [[1, 0, 0]]
    monkeypatch.setattr(improvements.llm, 'embedder', lambda: SimpleNamespace(embed=delete_during_embedding))
    assert improvements.process_one()
    with Session() as db:
        assert db.get(AnswerReview, review.id) is None and db.scalar(select(QAPair.id)) is None


def _conversation(review):
    from app.db import Message
    with Session() as db:
        return db.get(Message, review.message_id).conversation_id


def test_widget_explicit_uncertainty_is_recorded_even_with_retrieved_sources(client, tenant, chats, monkeypatch):
    monkeypatch.setattr(rag, 'retrieve', lambda *args: [rag.Match('Shipping policy only.', 'Policy.md', None, None)])
    chats['openai'].tool_turns = [
        lambda results, enabled: ('', [('offer_handoff', {})]),
        lambda results, enabled: ('I do not have the refund rule. Our team can help.', []),
    ]
    review, _ = widget(client, tenant)
    assert review.unanswered
    with Session() as db:
        assert not db.get(Conversation, _conversation(review)).handoff
