from tests.test_api import upload


def test_keyword_search_finds_exact_codes(client, tenant, chats, monkeypatch):
    from app.config import settings
    upload(client, tenant, 'catalog.txt', 'Model SKU-4471 is a ceramic planter, 30 cm wide.')
    upload(client, tenant, 'other.txt', 'Model SKU-9000 is a steel watering can.')
    # Switch semantic search off, so the only way to find the planter is the exact code.
    monkeypatch.setattr(settings, 'retrieval_max_distance', -1.0)
    r = client.post(f"/api/v1/chat/{tenant['public_key']}", json={'message': 'Tell me about SKU-4471'})
    assert [s['filename'] for s in r.json()['sources']] == ['catalog.txt']
    assert 'ceramic planter' in chats['openai'].calls[-1]['system']


def test_sources_carry_heading(client, tenant):
    doc = b'# Policies\n\n## Refunds\n\nRefund within 30 days.'
    r = client.post('/api/v1/sources', headers=tenant['headers'], files={'file': ('policy.md', doc, 'text/markdown')})
    assert r.status_code == 202
    body = client.post(f"/api/v1/chat/{tenant['public_key']}", json={'message': 'refund?'}).json()
    assert body['sources'][0] == {'number': 1, 'filename': 'policy.md', 'heading': 'Policies > Refunds', 'page': None}


def test_follow_up_is_rewritten_before_search(client, tenant, chats):
    upload(client, tenant, 'refunds.txt', 'Refund within 30 days.')
    url = f"/api/v1/chat/{tenant['public_key']}"
    first = client.post(url, json={'message': 'Tell me about refunds'}).json()
    assert chats['openai'].rewrites == []  # nothing to rewrite on the first message

    # "How long?" alone matches nothing; the rewritten query carries the refund topic.
    second = client.post(url, json={'message': 'How long?', 'conversation_id': first['conversation_id']}).json()
    assert len(chats['openai'].rewrites) == 1
    assert [s['filename'] for s in second['sources']] == ['refunds.txt']
    # The answer still sees the customer's own words.
    assert chats['openai'].calls[-1]['messages'][-1]['content'] == 'How long?'


def test_rewrite_failure_falls_back_to_question(client, tenant, chats, monkeypatch):
    from app import rag
    original = chats['openai'].complete

    def flaky(model, system, messages):
        if system == rag.REWRITE_PROMPT:
            raise RuntimeError('timeout')
        return original(model, system, messages)
    monkeypatch.setattr(chats['openai'], 'complete', flaky)
    url = f"/api/v1/chat/{tenant['public_key']}"
    first = client.post(url, json={'message': 'hello'}).json()
    r = client.post(url, json={'message': 'again', 'conversation_id': first['conversation_id']})
    assert r.status_code == 200


def test_eval_run_scores_cases(client, tenant, chats, monkeypatch):
    upload(client, tenant, 'refunds.txt', 'Refund within 30 days.')
    answers = {'How do refunds work?': 'Refunds are accepted within 30 days [1].',
               'Do you sell cars?': "I don't know based on our documents."}
    original = chats['openai'].complete
    monkeypatch.setattr(chats['openai'], 'complete',
                        lambda m, s, msgs: answers.get(msgs[-1]['content']) or original(m, s, msgs))
    cases = [
        {'question': 'How do refunds work?', 'expected_keywords': ['30 days'], 'expected_source': 'refunds.txt'},
        {'question': 'How do refunds work?', 'expected_keywords': ['60 days']},  # wrong expectation: must fail
        {'question': 'Do you sell cars?', 'expect_no_answer': True},
    ]
    r = client.post('/api/v1/evals', headers=tenant['headers'], json={'cases': cases})
    assert r.status_code == 201 and len(r.json()) == 3
    report = client.post('/api/v1/evals/run', headers=tenant['headers']).json()
    assert [x['passed'] for x in report['results']] == [True, False, True]
    assert report['results'][1]['missing_keywords'] == ['60 days']
    assert report['accuracy'] == 0.667 and report['source_hit_rate'] == 1.0

    case_id = report['results'][1]['id']
    assert client.delete(f'/api/v1/evals/{case_id}', headers=tenant['headers']).status_code == 204
    assert len(client.get('/api/v1/evals', headers=tenant['headers']).json()) == 2


def test_eval_run_needs_cases(client, tenant):
    assert client.post('/api/v1/evals/run', headers=tenant['headers']).status_code == 409
