"""Synthetic accounts for Meta channel controls; never uses live assets."""
from app.db import Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Meta DM Demo', public_key='pub_' + uid(), admin_key_hash=hash_token(uid()))
    db.add(tenant)
    db.flush()
    for address, role in [('meta-dm@example.com', 'owner'), ('meta-viewer@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Meta Demo User', email_verified_at=now(),
                    password_hash=hash_password('meta-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
