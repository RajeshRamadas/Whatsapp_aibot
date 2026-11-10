"""Synthetic OCR browser accounts and a scan generated without external services."""
from pathlib import Path

from app.db import Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token
from tests.ocr_fixtures import scan

Path('/tmp/chatbot-ocr-fixture.png').write_bytes(scan())
with Session.begin() as db:
    tenant = Tenant(id=uid(), name='OCR Browser Workspace', public_key='pub_' + uid(), admin_key_hash=hash_token(uid()))
    db.add(tenant)
    db.flush()
    for address, role in [('ocr@example.com', 'owner'), ('ocr-reader@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='OCR Browser User', email_verified_at=now(),
                    password_hash=hash_password('ocr-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
