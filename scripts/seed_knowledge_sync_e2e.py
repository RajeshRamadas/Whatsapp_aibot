"""Synthetic workspace for cloud knowledge acceptance, with no external accounts."""
from app.db import Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Cloud knowledge demo', public_key='pub_knowledge_sync_demo',
                    admin_key_hash=hash_token(uid()), allowed_origins='["http://localhost:3000"]', bot_live=True)
    db.add(tenant)
    db.flush()
    for address, role in [('knowledge-sync@example.com', 'owner'), ('knowledge-reader@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Knowledge demo user', email_verified_at=now(),
                    password_hash=hash_password('knowledge-sync-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
