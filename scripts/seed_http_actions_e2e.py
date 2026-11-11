"""Synthetic action workspace without any configured external service."""
from app.db import Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Actions Demo', public_key='pub_actions_demo', admin_key_hash=hash_token(uid()),
                    allowed_origins='["http://localhost:3000"]', bot_live=True)
    db.add(tenant)
    db.flush()
    for address, role in [('actions@example.com', 'owner'), ('actions-viewer@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Actions Demo User', email_verified_at=now(),
                    password_hash=hash_password('actions-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
