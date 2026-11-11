"""Regenerate the public contract: python -m scripts.generate_public_openapi."""
import json
from pathlib import Path

from app.public_api import app

if __name__ == '__main__':
    path = Path(__file__).resolve().parents[1] / 'docs' / 'public-api.openapi.json'
    path.write_text(json.dumps(app.openapi(), indent=2) + '\n')
