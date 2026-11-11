"""Synthetic outgoing-event workspace, without any configured receiver."""
from app.db import Conversation, Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Events Demo', public_key='pub_' + uid(), admin_key_hash=hash_token(uid()))
    db.add(tenant)
    db.flush()
    for email, role in [('webhooks@example.com', 'owner'), ('webhooks-viewer@example.com', 'viewer')]:
        user = User(id=uid(), email=email, name='Events Demo User', email_verified_at=now(),
                    password_hash=hash_password('webhooks-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
    db.add(Conversation(tenant_id=tenant.id, channel='web', external_user='synthetic-events-customer'))
