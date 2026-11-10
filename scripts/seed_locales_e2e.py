"""Isolated English/Hindi admin demo accounts; no real customer content."""
from app.db import Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Language Demo', public_key='pub_' + uid(), admin_key_hash=hash_token(uid()))
    db.add(tenant)
    db.flush()
    for address, role in [('language@example.com', 'owner'), ('language-reader@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Language Demo User', email_verified_at=now(),
                    password_hash=hash_password('language-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
