from app.db import Session, Source, uid


def test_onboarding_tenant_scoped_and_auth(client, tenant):
    url = '/api/v1/onboarding'
    assert client.get(url).status_code == 401
    r = client.get(url, headers=tenant['headers'])
    assert r.status_code == 200
    assert r.json() == {'connect_whatsapp': False, 'add_knowledge': False,
                        'test_bot': False, 'go_live': False}
    with Session.begin() as db:
        db.add(Source(id=uid(), tenant_id=tenant['tenant_id'], filename='a.txt',
                      kind='file', status='ready', file_path='test'))
    assert client.get(url, headers=tenant['headers']).json()['add_knowledge'] is True
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    assert client.get(url, headers={'X-Admin-Key': other['admin_key']}).json()['add_knowledge'] is False
    assert client.get(url, headers={**tenant['headers'], 'X-Workspace-Id': other['tenant_id']}).status_code == 404
