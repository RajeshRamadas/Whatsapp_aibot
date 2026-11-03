from uuid import uuid4

from app.services.widget_turns import process_turn


def test_widget_turn_persists_streams_and_accepts_feedback(client, tenant):
    base = '/api/v1/chat/' + tenant['public_key'] + '/widget'
    headers = {'Origin': 'https://shop.example'}
    config = client.get(base + '/config', headers=headers)
    assert config.status_code == 200 and config.headers['access-control-allow-origin'] == 'https://shop.example'
    assert client.get(base + '/config', headers={'Origin': 'https://foreign.example'}).status_code == 403
    settings = client.put('/api/v1/widget-settings', headers=tenant['headers'], json={'position': 'left'})
    assert settings.status_code == 200
    assert client.get(base + '/config').json()['position'] == 'left'
    body = {'request_id': str(uuid4()), 'message': 'What are your hours?'}
    queued = client.post(base + '/turns', headers=headers, json=body)
    assert queued.status_code == 202 and queued.json()['state'] == 'queued'
    assert client.post(base + '/turns', headers=headers, json=body).json() == queued.json()
    assert process_turn()
    assert not process_turn()
    path = base + '/turns/' + body['request_id']
    assert client.get(path).json()['answer'].endswith(body['message'])
    assert 'event: result' in client.get(path + '/events').text
    assert client.post(path + '/feedback', json={'vote': 'up'}).json() == {'feedback': 'up'}
    assert client.get(path).json()['feedback'] == 'up'
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other widget'}).json()
    assert client.get(path.replace(tenant['public_key'], other['public_key'])).status_code == 404


def test_widget_handoff_reaches_inbox_and_agent_reply_returns(client, tenant, chats):
    base = '/api/v1/chat/' + tenant['public_key'] + '/widget'
    queued = client.post(base + '/turns', json={
        'request_id': str(uuid4()), 'message': 'Talk to a person', 'handoff': True,
    }).json()
    assert process_turn()
    assert chats['openai'].calls == []
    conversation = queued['conversation_id']
    path = f'/api/v1/inbox/conversations/{conversation}'
    headers = tenant['headers']
    assert client.get(path, headers=headers).json()['status'] == 'waiting'
    response = client.post(path + '/reply', headers=headers, json={'message': 'I can help.'})
    assert response.status_code == 200 and response.json()['status'] == 'sent'
    replies = client.get(base + '/conversations/' + conversation + '/replies').json()
    assert replies[0]['text'] == 'I can help.'
