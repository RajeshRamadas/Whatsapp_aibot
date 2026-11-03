from app import rag
from app.db import Conversation, Session, uid


def test_scripted_capture_and_management(client, tenant, chats):
    headers = tenant['headers']
    config = {'fields': {'name': 'required', 'need': 'optional'}}
    assert client.put('/api/v1/lead-config', headers=headers, json=config).status_code == 200
    assert client.get('/api/v1/lead-config', headers=headers).json() == config
    chats['openai'].tool_turns = [
        lambda results, enabled: ('', [('capture_lead', {'name': 'Sam', 'need': '=example'})]),
        lambda results, enabled: ('Thanks, Sam.', []),
    ]
    response = client.post('/api/v1/chat/' + tenant['public_key'], json={'message': 'Please follow up. I am Sam.'})
    assert response.status_code == 200, response.text
    lead_id = response.json()['conversation_id']
    [lead] = client.get('/api/v1/leads', headers=headers).json()
    assert lead['fields'] == {'name': 'Sam', 'need': '=example'}
    with Session() as db:
        conversation = db.get(Conversation, lead_id)
    rag.save_exchange(conversation, lead_fields={'name': 'Samuel'})
    path = '/api/v1/leads/' + lead_id
    assert client.patch(path, headers=headers, json={'status': 'contacted'}).json()['status'] == 'contacted'
    assert client.patch(path, headers=headers, json={'owner_id': uid()}).status_code == 422
    assert client.get('/api/v1/leads?status=new', headers=headers).json() == []
    export = client.get('/api/v1/leads/export', headers=headers)
    assert "Samuel" in export.text and "'=example" in export.text
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    other_headers = {'X-Admin-Key': other['admin_key']}
    assert client.get('/api/v1/leads', headers=other_headers).json() == []
    assert client.patch(path, headers=other_headers, json={'notes': 'Wrong workspace'}).status_code == 404
    assert client.get('/api/v1/leads').status_code == 401


def test_reject_unknown_lead_fields(client, tenant):
    assert client.put('/api/v1/lead-config', headers=tenant['headers'],
                      json={'fields': {'secret': 'required'}}).status_code == 422
