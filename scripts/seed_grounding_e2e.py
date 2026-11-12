"""Synthetic grounding workspace; model and verifier are offline browser fakes."""
from app.db import Chunk, Membership, Session, Source, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Grounding Demo', public_key='pub_grounding_demo',
        admin_key_hash=hash_token(uid()), allowed_origins='["http://localhost:3000"]', bot_live=True)
    db.add(tenant)
    db.flush()
    user = User(id=uid(), email='grounding@example.com', name='Grounding Demo User',
                email_verified_at=now(), password_hash=hash_password('grounding-browser-passphrase'))
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role='owner'))
    source = Source(id=uid(), tenant_id=tenant.id, filename='refund-policy.txt', kind='txt',
                    status='ready', file_path='browser-fixture')
    db.add(source)
    db.flush()
    db.add(Chunk(tenant_id=tenant.id, source_id=source.id,
                 content='Refunds accepted within 30 days.', embedding=[1, 0, 0]))
