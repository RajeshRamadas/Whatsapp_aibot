"""Synthetic API-key management workspace; never connects to a provider."""
from app.db import Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Developer Demo', public_key='pub_' + uid(), admin_key_hash=hash_token(uid()))
    db.add(tenant)
    db.flush()
    for address, role in [('developer@example.com', 'owner'), ('developer-viewer@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Developer Demo User', email_verified_at=now(),
                    password_hash=hash_password('developer-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
