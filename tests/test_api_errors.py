"""Contract tests for the T1.2 structured API errors and router composition."""


def test_missing_admin_key_uses_error_envelope(client):
    response = client.get('/api/v1/tenant')
    assert response.status_code == 401
    assert response.json() == {'error': {'code': 'unauthorized', 'message': 'Authentication required', 'details': None}}


def test_validation_error_uses_error_envelope(client):
    response = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                           json={'name': 'x'})
    assert response.status_code == 422
    error = response.json()['error']
    assert error['code'] == 'validation_error'
    assert error['message'] == 'Invalid request'
    assert error['details'][0]['location'] == ['body', 'name']


def test_routers_keep_existing_health_endpoint(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
