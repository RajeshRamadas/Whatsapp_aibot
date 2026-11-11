"""Synthetic payment workspace; all merchant API calls use the browser app's fake."""
from app.db import Conversation, Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Payment Demo', public_key='pub_' + uid(),
                    admin_key_hash=hash_token(uid()), timezone='Asia/Calcutta')
    db.add(tenant)
    db.flush()
    for address, role in [('payments@example.com', 'owner'), ('payment-agent@example.com', 'agent'),
                          ('payment-reader@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Payment Demo User', email_verified_at=now(),
                    password_hash=hash_password('payment-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
    db.add(Conversation(tenant_id=tenant.id, channel='web', external_user='Payment customer'))
