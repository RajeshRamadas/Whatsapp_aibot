"""Synthetic multi-workspace account for authenticator and isolation browser checks."""
from app.db import Conversation, Lead, Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    user = User(id=uid(), email='security@example.com', name='Security Demo User', email_verified_at=now(),
                password_hash=hash_password('security-browser-passphrase'))
    viewer = User(id=uid(), email='security-reader@example.com', name='Reader Demo User', email_verified_at=now(),
                  password_hash=hash_password('security-browser-passphrase'))
    agency = User(id=uid(), email='agency@example.com', name='Agency Demo User', email_verified_at=now(),
                  password_hash=hash_password('security-browser-passphrase'))
    db.add_all([user, viewer, agency])
    db.flush()
    for name, role, customer in [('Agency Alpha', 'owner', 'Alpha customer'), ('Agency Beta', 'admin', 'Beta customer')]:
        tenant = Tenant(id=uid(), name=name, public_key='pub_' + uid(), admin_key_hash=hash_token(uid()))
        db.add(tenant)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role='owner'))
        db.add(Membership(user_id=agency.id, tenant_id=tenant.id, role=role))
        if role == 'owner':
            db.add(Membership(user_id=viewer.id, tenant_id=tenant.id, role='viewer'))
        conversation = Conversation(id=uid(), tenant_id=tenant.id, channel='web', external_user=customer)
        db.add(conversation)
        db.flush()
        db.add(Lead(conversation_id=conversation.id, tenant_id=tenant.id, fields={'name': customer}))
