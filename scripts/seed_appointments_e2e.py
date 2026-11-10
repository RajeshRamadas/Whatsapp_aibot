"""Synthetic appointment administrators and a customer chat; no outside services."""
from app.db import Conversation, Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Appointment Demo', public_key='pub_' + uid(),
                    admin_key_hash=hash_token(uid()), timezone='Asia/Calcutta', default_language='en')
    db.add(tenant)
    db.flush()
    for address, role in [('appointments@example.com', 'owner'), ('appointment-agent@example.com', 'agent'),
                          ('appointment-reader@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Appointment Demo User', email_verified_at=now(),
                    password_hash=hash_password('appointment-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
    db.add(Conversation(tenant_id=tenant.id, channel='web', external_user='Appointment customer'))
