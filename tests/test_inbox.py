from datetime import timedelta

from app.db import Conversation, Session, Tenant, now, uid


def make_conv(tenant_id, **kwargs):
    conversation_id = uid()
    with Session.begin() as db:
        db.add(Conversation(id=conversation_id, tenant_id=tenant_id, channel='whatsapp',
                            external_user=conversation_id, **kwargs))
    return conversation_id


def test_inbox_isolation_actions_and_counts(client, tenant, monkeypatch):
    monkeypatch.setattr('app.api.inbox.publish_sync', lambda *args: None)
    first = make_conv(tenant['tenant_id'])
    second = make_conv(tenant['tenant_id'])
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other Workspace'}).json()
    foreign = make_conv(other['tenant_id'])
    url = '/api/v1/inbox/conversations'
    assert client.get(url).status_code == 401
    result = client.get(url, headers=tenant['headers'])
    assert result.status_code == 200
    assert {row['id'] for row in result.json()['items']} == {first, second}
    assert result.json()['counts'] == {'bot': 2}
    page = client.get(url, headers=tenant['headers'], params={'limit': 1}).json()
    assert len(page['items']) == 1
    assert page['next_cursor']
    next_page = client.get(url, headers=tenant['headers'],
                           params={'limit': 1, 'cursor': page['next_cursor']}).json()
    assert len(next_page['items']) == 1
    assert next_page['items'][0]['id'] != page['items'][0]['id']
    actions = [
        ('POST', 'take-over', None), ('POST', 'assign', {'user_id': None}),
        ('POST', 'note', {'text': 'Private'}), ('POST', 'reply', {'message': 'Hello'}),
        ('POST', 'hand-back', None), ('POST', 'close', None), ('POST', 'reopen', None),
        ('PUT', 'labels', {'labels': ['vip']}), ('POST', 'typing', None),
    ]
    for method, action, body in actions:
        assert client.request(method, f'{url}/{first}/{action}', json=body).status_code == 401
        if action != 'take-over':
            assert client.request(method, f'{url}/{foreign}/{action}', headers=tenant['headers'],
                                  json=body).status_code == 404
    assert client.post(f'{url}/{first}/typing', headers=tenant['headers']).json()['expires_in_seconds'] == 5
    assert client.post(f'{url}/{first}/assign', headers=tenant['headers'],
                       json={'user_id': None}).json()['status'] == 'waiting'
    assert client.post(f'{url}/{first}/take-over', headers=tenant['headers']).status_code == 403
    assert client.post(f'{url}/{first}/close', headers=tenant['headers']).status_code == 200
    assert client.get(url, headers=tenant['headers'], params={'status': 'closed'}).json()['items'][0]['id'] == first
    assert client.post(f'{url}/{first}/reopen', headers=tenant['headers']).json()['status'] == 'waiting'
    assert client.post(f'{url}/{first}/hand-back', headers=tenant['headers']).json()['handoff'] is False
    assert client.put(f'{url}/{first}/labels', headers=tenant['headers'],
                      json={'labels': ['vip', 'vip']}).json()['labels'] == ['vip']
    assert client.post(f'{url}/{first}/note', headers=tenant['headers'],
                       json={'text': 'Follow up tomorrow'}).status_code == 200
    assert client.post(f'{url}/{first}/assign', headers=tenant['headers'],
                       json={'user_id': uid()}).status_code == 422


def test_whatsapp_window_and_canned_replies(client, tenant, monkeypatch):
    monkeypatch.setattr('app.api.inbox.publish_sync', lambda *args: None)
    with Session.begin() as db:
        db.get(Tenant, tenant['tenant_id']).whatsapp_phone_number_id = 'test-phone-id'
    conversation_id = make_conv(tenant['tenant_id'], last_inbound_at=now() - timedelta(days=2))
    base = f'/api/v1/inbox/conversations/{conversation_id}'
    response = client.post(base + '/reply', headers=tenant['headers'], json={'message': 'Hello'})
    assert response.status_code == 409
    assert 'whatsapp_window_expired' in response.json()['error']['message']
    canned = '/api/v1/inbox/canned-replies'
    created = client.post(canned, headers=tenant['headers'],
                          json={'title': 'Welcome', 'body': 'Hello there'})
    assert created.status_code == 201
    reply_id = created.json()['id']
    assert client.get(canned, headers=tenant['headers']).json()[0]['id'] == reply_id
    assert client.put(f'{canned}/{reply_id}', headers=tenant['headers'],
                      json={'title': 'Welcome', 'body': 'Updated'}).status_code == 200
    assert client.get(canned, headers=tenant['headers']).json()[0]['body'] == 'Updated'
    assert client.delete(f'{canned}/{reply_id}', headers=tenant['headers']).status_code == 204
    assert client.get(canned, headers=tenant['headers']).json() == []
