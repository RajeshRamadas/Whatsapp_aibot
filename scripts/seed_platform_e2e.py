"""Isolated platform-admin fixtures for the local/CI browser database."""
import secrets

from app.db import Membership, Session, Source, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    admin = User(id=uid(), email='platform@example.com', name='Platform Administrator',
                 password_hash=hash_password('platform-browser-passphrase'), is_platform_admin=True,
                 email_verified_at=now())
    member = User(id=uid(), email='platform-member@example.com', name='Platform Test Member',
                  password_hash=hash_password('platform-member-passphrase'), email_verified_at=now())
    tenant = Tenant(name='Platform Suspension Fixture', admin_key_hash=hash_token(secrets.token_urlsafe(32)),
                    public_key='pub_' + secrets.token_urlsafe(18), bot_live=True)
    db.add_all([admin, member, tenant])
    db.flush()
    db.add(Membership(user_id=member.id, tenant_id=tenant.id, role='owner'))
    db.add(Source(tenant_id=tenant.id, filename='private-document.pdf', kind='pdf', file_path='private-path',
                  status='failed', error='Private customer content must never appear in platform administration'))
