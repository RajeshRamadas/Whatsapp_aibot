"""Synthetic lead-sync workspace for offline browser tests."""
from app.db import Conversation, Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token
from app.services.leads import save_lead

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Lead Sync Demo', public_key='pub_' + uid(), admin_key_hash=hash_token(uid()))
    db.add(tenant)
    db.flush()
    for address, role in [('lead-sync@example.com', 'owner'), ('lead-agent@example.com', 'agent'),
                          ('lead-viewer@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Lead Sync Demo User', email_verified_at=now(),
                    password_hash=hash_password('lead-sync-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
    conversation = Conversation(id=uid(), tenant_id=tenant.id, channel='web', external_user='Demo customer')
    db.add(conversation)
    db.flush()
    save_lead(db, conversation, {'name': 'Demo customer', 'phone': '+919999000001', 'email': 'customer@example.com'})
