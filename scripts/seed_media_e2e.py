"""Isolated media browser accounts; no real phone, provider or customer data."""
from app.db import Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Media Browser Workspace', public_key='pub_' + uid(),
                    admin_key_hash=hash_token(uid()), whatsapp_phone_number_id='MEDIA_BROWSER', bot_live=False)
    db.add(tenant)
    db.flush()
    for address, role in [('media@example.com', 'owner'), ('media-reader@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Media Browser User', email_verified_at=now(),
                    password_hash=hash_password('media-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
