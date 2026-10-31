import json

from tests.conftest import signed, wa_payload


def link_number(client, tenant, pnid='PNID1'):
    r = client.patch('/api/v1/tenant', headers=tenant['headers'], json={'whatsapp_phone_number_id': pnid})
    assert r.status_code == 200, r.text


def post(client, payload):
    raw, headers = signed(payload)
    return client.post('/webhooks/whatsapp', content=raw, headers=headers)


def test_verification_handshake(client):
    q = {'hub.mode': 'subscribe', 'hub.verify_token': 'verify-me', 'hub.challenge': '12345'}
    r = client.get('/webhooks/whatsapp', params=q)
    assert r.status_code == 200 and r.text == '12345'
    assert client.get('/webhooks/whatsapp', params={**q, 'hub.verify_token': 'bad'}).status_code == 403


def test_rejects_bad_signature(client, tenant, sent):
    link_number(client, tenant)
    raw = json.dumps(wa_payload()).encode()
    r = client.post('/webhooks/whatsapp', content=raw, headers={'X-Hub-Signature-256': 'sha256=deadbeef'})
    assert r.status_code == 401
    assert client.post('/webhooks/whatsapp', content=raw).status_code == 401
    assert sent == []


def test_answers_text_and_dedupes_retries(client, tenant, sent, chats):
    link_number(client, tenant)
    assert post(client, wa_payload('What are your hours?')).status_code == 200
    assert post(client, wa_payload('What are your hours?')).status_code == 200  # Meta retry, same wamid
    assert sent == [('PNID1', '15551234567', 'openai:gpt-4.1-mini: What are your hours?')]

    post(client, wa_payload('And refunds?', msg_id='wamid.2'))
    assert len(sent) == 2
    assert [t['role'] for t in chats['openai'].calls[-1]['messages']] == ['user', 'assistant', 'user']

    [conv] = client.get('/api/v1/conversations', headers=tenant['headers']).json()
    assert conv['channel'] == 'whatsapp' and conv['user'] == '15551234567'


def test_unknown_number_and_status_updates_ignored(client, tenant, sent):
    link_number(client, tenant)
    assert post(client, wa_payload(phone_number_id='OTHER')).status_code == 200
    status_only = {'entry': [{'changes': [{'value': {'metadata': {'phone_number_id': 'PNID1'},
                                                     'statuses': [{'id': 'wamid.x', 'status': 'read'}]}}]}]}
    assert post(client, status_only).status_code == 200
    assert sent == []


def test_non_text_message(client, tenant, sent):
    link_number(client, tenant)
    post(client, wa_payload(kind='image'))
    assert sent[0][2].startswith('Sorry, I can only read text')
    [conv] = client.get('/api/v1/conversations', headers=tenant['headers']).json()
    transcript = client.get(f"/api/v1/conversations/{conv['id']}/messages", headers=tenant['headers']).json()
    assert [(m['role'], m['content']) for m in transcript] == [('user', '[image message]'), ('assistant', sent[0][2])]


def test_handoff_to_human_and_back(client, tenant, sent, chats):
    link_number(client, tenant)
    post(client, wa_payload('human'))
    assert 'team will reply' in sent[-1][2]
    [conv] = client.get('/api/v1/conversations?handoff=true', headers=tenant['headers']).json()

    post(client, wa_payload('hello?', msg_id='wamid.2'))
    assert len(sent) == 1 and chats['openai'].calls == []  # bot stays silent

    r = client.post(f"/api/v1/conversations/{conv['id']}/reply", headers=tenant['headers'],
                    json={'message': 'Hi, this is Sam.'})
    assert r.status_code == 200 and sent[-1] == ('PNID1', '15551234567', 'Hi, this is Sam.')

    client.post(f"/api/v1/conversations/{conv['id']}/handoff?active=false", headers=tenant['headers'])
    post(client, wa_payload('thanks', msg_id='wamid.3'))
    assert sent[-1][2] == 'openai:gpt-4.1-mini: thanks'
    # Agent messages are kept in the transcript but not replayed to the model.
    roles = [m['role'] for m in client.get(f"/api/v1/conversations/{conv['id']}/messages",
                                           headers=tenant['headers']).json()]
    assert roles == ['user', 'assistant', 'user', 'agent', 'user', 'assistant']


def test_number_cannot_be_claimed_twice(client, tenant):
    link_number(client, tenant)
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    r = client.patch('/api/v1/tenant', headers={'X-Admin-Key': other['admin_key']},
                     json={'whatsapp_phone_number_id': 'PNID1'})
    assert r.status_code == 409


def test_send_failure_propagates_for_worker_retry(client, tenant, chats, monkeypatch):
    from app import whatsapp
    calls = []

    def flaky(pid, to, body):
        calls.append(body)
        raise RuntimeError('graph api down')
    monkeypatch.setattr(whatsapp, 'send_text', flaky)
    link_number(client, tenant)
    assert post(client, wa_payload('hi')).status_code == 200
    assert len(calls) == 1  # inline test worker propagates provider errors
