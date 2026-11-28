from sqlalchemy import select

from app.db import AuditLog, Session, engine
from app.services import branding
from tests.test_agency_billing import workspaces

SETTINGS = '/api/v1/agency/branding'
PUBLIC = '/api/v1/branding'
DOMAIN = 'agency.example.com'
INPUT = {'display_name': 'Beautiva Agency', 'domain': DOMAIN, 'logo_url': 'https://assets.example.com/logo.png',
         'primary_color': '#1254ff', 'secondary_color': '#f4e4ff'}


def test_domain_proof_and_workspace_scope(client, monkeypatch):
    payer, managed, headers, child = workspaces(client, monkeypatch)
    assert client.get(SETTINGS, headers=headers).json()['display_name'] == 'Agency'
    for invalid in ({'domain': 'https://agency.example.com/path'}, {'domain': '127.0.0.1'},
                    {'logo_url': 'http://example.com/logo.png'}, {'primary_color': 'red'}):
        assert client.put(SETTINGS, headers=headers, json={**INPUT, **invalid}).status_code == 422
    r = client.put(SETTINGS, headers=headers, json={**INPUT, 'domain': 'Agency.Example.com.'})
    assert r.status_code == 200 and r.json()['domain'] == DOMAIN
    assert r.json()['dns_name'] == f'_chatbot-verification.{DOMAIN}'
    token = r.json()['dns_value']
    assert client.get(PUBLIC, params={'domain': DOMAIN}).json() is None
    assert client.get(PUBLIC + '/domain', params={'domain': DOMAIN}).status_code == 404
    monkeypatch.setattr(branding, 'proves_ownership', lambda *_: False)
    assert client.post(SETTINGS + '/verify', headers=headers).status_code == 422

    def proof(host, challenge):
        assert engine.pool.checkedout() == 0
        return (host, challenge) == (DOMAIN, token)

    monkeypatch.setattr(branding, 'proves_ownership', proof)
    assert client.post(SETTINGS + '/verify', headers=headers).json()['verified_at'] is not None
    response = client.get(PUBLIC, params={'domain': DOMAIN})
    assert response.json() == {k: v for k, v in INPUT.items() if k != 'domain'}
    assert response.headers['cache-control'] == 'no-store'
    assert client.get(PUBLIC + '/domain', headers={branding.FORWARDED_HOST: DOMAIN}).status_code == 204
    assert client.get(PUBLIC + '/domain', params={'domain': DOMAIN}).status_code == 204
    branded = {'Host': DOMAIN, branding.FORWARDED_HOST: 'canonical.example.com'}
    assert [row['tenant_id'] for row in client.get('/api/v1/workspaces', headers=branded).json()] == [payer]
    assert client.get('/api/v1/tenant', headers={**child, **branded}).status_code == 404
    assert client.get(SETTINGS, headers={**child, **branded}).status_code == 404
    assert client.put(f'/api/v1/agency/clients/{managed}', headers=headers).status_code == 200
    assert client.get('/api/v1/tenant', headers={**child, **branded}).status_code == 200
    assert client.get(SETTINGS, headers=child).status_code == 409
    assert client.put(SETTINGS, headers=headers, json={**INPUT, 'domain': 'other.example.com'}).status_code == 200
    assert client.get(PUBLIC + '/domain', params={'domain': DOMAIN}).status_code == 404
    with Session() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.tenant_id == payer,
                                               AuditLog.action == 'agency.domain_verified'))
        assert row and token not in str(row.details)


def test_pending_domain_does_not_reserve_it_but_verification_is_exclusive(client, monkeypatch):
    _, _, headers, other = workspaces(client, monkeypatch)
    for owner in (headers, other):
        assert client.put(SETTINGS, headers=owner, json=INPUT).status_code == 200
    monkeypatch.setattr(branding, 'proves_ownership', lambda *_: True)
    assert client.post(SETTINGS + '/verify', headers=other).status_code == 200
    assert client.post(SETTINGS + '/verify', headers=headers).status_code == 409
    client.cookies.clear()
    assert client.get(SETTINGS).status_code == 401
    assert client.put(SETTINGS, json=INPUT).status_code == 401
