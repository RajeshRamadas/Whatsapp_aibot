"""Synthetic accuracy workspace and knowledge; browser tests use fake models."""
from app.db import Chunk, Membership, Session, Source, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Accuracy Demo', public_key='pub_' + uid(), admin_key_hash=hash_token(uid()))
    db.add(tenant)
    db.flush()
    for address, role in [('accuracy@example.com', 'owner'), ('accuracy-viewer@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Accuracy Demo User', email_verified_at=now(),
                    password_hash=hash_password('accuracy-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
    source = Source(id=uid(), tenant_id=tenant.id, filename='accuracy-refunds.txt', kind='txt',
                    status='ready', file_path='browser-fixture')
    db.add(source)
    db.flush()
    db.add(Chunk(tenant_id=tenant.id, source_id=source.id, content='Refunds accepted within 30 days.', embedding=[1, 0, 0]))
