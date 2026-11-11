"""The published contract must match routes and document every POST's replay header."""
import json
from pathlib import Path

from app.public_api import app


def test_published_contract():
    schema = app.openapi()
    path = Path(__file__).resolve().parents[1] / 'docs' / 'public-api.openapi.json'
    assert json.loads(path.read_text()) == schema
    for operations in schema['paths'].values():
        if 'post' in operations:
            assert any(p['name'] == 'Idempotency-Key' and p['required'] for p in operations['post']['parameters'])
        for route in operations.values():
            assert route['responses']['422']['content']['application/json']['schema'] == {
                '$ref': '#/components/schemas/ErrorResponse'}
    assert schema['components']['schemas']['ErrorItem']['properties']['details']['type'] == 'object'
