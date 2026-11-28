"""Workspace creation with an opt-in, tenant-scoped industry template."""
from pathlib import Path

from tests.test_workspaces import Recorder, login

START = '/api/v1/workspaces'
TEMPLATE = 'beauty-salon'


def test_beauty_salon_template_seeds_only_the_new_workspace(client, monkeypatch):
    monkeypatch.setattr('app.config.settings.auth_cookie_secure', False)
    assert client.get(START + '/templates').status_code == 401
    headers = login(client, 'salon-owner@example.com', Recorder())
    response = client.get(START + '/templates', headers=headers)
    assert response.status_code == 200
    template, = response.json()
    assert template['id'] == TEMPLATE and template['name']['en'] == 'Beauty salon'
    assert all(item['hi'] for item in template['suggested_knowledge'])
    assert template['sample_qa'] and 'bot_rules' not in template

    for invalid in ('unknown', '../email.en', ''):
        response = client.post(START, headers=headers, json={'name': 'Salon', 'industry_template': invalid})
        assert response.status_code == 422
    assert client.get(START, headers=headers).json() == []
    response = client.post(START, headers=headers, json={'name': 'Salon', 'industry_template': TEMPLATE})
    assert response.status_code == 201
    created = response.json()
    assert created['industry_template'] == TEMPLATE and created['bot_tone'] == 'friendly'
    assert 'booking system confirms it' in created['bot_rules'] and created['bot_live'] is False
    salon = {**headers, 'X-Workspace-Id': created['tenant_id']}
    assert client.get('/api/v1/lead-config', headers=salon).json()['fields'] == {
        'name': 'required', 'need': 'required', 'preferred_time': 'optional',
    }
    onboarding = client.get('/api/v1/onboarding', headers=salon).json()
    assert onboarding['suggested_knowledge'] == template['suggested_knowledge']
    cases = client.get('/api/v1/evals', headers=salon).json()
    assert len(cases) == 10 and sum(case['expect_no_answer'] for case in cases) == 2
    assert {case['expected_source'] for case in cases if not case['expect_no_answer']} == {'beauty-salon-sample.txt'}
    reference = (Path(__file__).resolve().parents[1] / 'docs/examples/beauty-salon-sample.txt').read_text()
    assert all(word.lower() in reference.lower() for case in cases for word in case['expected_keywords'])

    generic = client.post(START, headers=headers, json={'name': 'General workspace'}).json()
    other = {**headers, 'X-Workspace-Id': generic['tenant_id']}
    assert generic['industry_template'] is None and generic['bot_tone'] == 'neutral'
    assert client.get('/api/v1/evals', headers=other).json() == []
    assert client.get('/api/v1/lead-config', headers=other).json()['fields'] == {}
    assert 'suggested_knowledge' not in client.get('/api/v1/onboarding', headers=other).json()
    assert client.delete('/api/v1/evals/' + cases[0]['id'], headers=other).status_code == 404
