from tests.test_api import upload

URL = '/api/v1/qa-pairs'
PAIR = {'question': 'What is the refund period?', 'answer': 'Refunds are accepted within 14 days.'}


def test_faq_wins_over_conflicting_document_in_eval(client, tenant, chats, monkeypatch):
    headers = tenant['headers']
    upload(client, tenant, 'refunds.txt', 'Refunds are accepted within 30 days.')
    assert client.post(URL, headers=headers, json=PAIR).status_code == 201

    def grounded_answer(model, system, messages):
        assert PAIR['answer'] in system
        assert '30 days' not in system
        return PAIR['answer'] + ' [1]'

    monkeypatch.setattr(chats['openai'], 'complete', grounded_answer)
    cases = [{'question': PAIR['question'], 'expected_keywords': ['14 days'], 'expected_source': 'FAQ'}]
    assert client.post('/api/v1/evals', headers=headers, json={'cases': cases}).status_code == 201
    from app.services.evaluation_runs import process_one
    queued = client.post('/api/v1/evals/run', headers=headers).json()
    assert process_one()
    result = client.get('/api/v1/evals/runs/' + queued['id'], headers=headers).json()
    assert result['results'][0]['passed']
    assert result['source_hit_rate'] == 1.0


def test_faq_crud_and_workspace_isolation(client, tenant):
    headers = tenant['headers']
    assert client.get(URL).status_code == 401
    assert client.post(URL, headers=headers, json={**PAIR, 'question': ' '}).status_code == 422
    response = client.post(URL, headers=headers, json=PAIR)
    assert response.status_code == 201
    pair = response.json()
    path = URL + '/' + pair['id']
    assert client.get(URL, headers=headers).json() == [pair]
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other FAQ workspace'}).json()
    foreign = {'X-Admin-Key': other['admin_key']}
    assert client.get(URL, headers=foreign).json() == []
    assert client.get(path, headers=foreign).status_code == 404
    assert client.put(path, headers=foreign, json=PAIR).status_code == 404
    assert client.delete(path, headers=foreign).status_code == 404
    update = {'question': 'Do you offer shipping?', 'answer': 'We ship free.'}
    assert client.put(path, headers=headers, json=update).json() == {**update, 'id': pair['id']}
    chat = '/api/v1/chat/' + tenant['public_key']
    assert client.post(chat, json={'message': 'refund'}).json()['sources'] == []
    assert client.post(chat, json={'message': 'shipping'}).json()['sources'][0]['filename'] == 'FAQ'
    assert client.delete(path, headers=headers).status_code == 204
    assert client.get(path, headers=headers).status_code == 404
