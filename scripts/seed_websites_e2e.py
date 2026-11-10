"""Synthetic website-knowledge accounts; no external fetches or model credentials."""
from app.db import Membership, Plan, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    plan = db.get(Plan, 'growth')
    plan.limits = {**plan.limits, 'crawl_pages': 50}
    tenant = Tenant(id=uid(), name='Website Knowledge Demo', plan_id='growth', public_key='pub_' + uid(),
                    admin_key_hash=hash_token(uid()))
    db.add(tenant)
    db.flush()
    for address, role in [('websites@example.com', 'owner'), ('websites-reader@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Website Demo User', email_verified_at=now(),
                    password_hash=hash_password('websites-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
