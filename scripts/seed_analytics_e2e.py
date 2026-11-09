"""Isolated, deterministic analytics records for browser CI."""
from datetime import timedelta

from app.db import Conversation, Lead, Membership, Message, Session, Tenant, User, now, uid
from app.services.analytics import record, reconcile
from app.services.auth import hash_password, hash_token

at = now().replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=1)
with Session.begin() as db:
    user = User(id=uid(), email='analytics@example.com', name='Analytics Viewer',
                password_hash=hash_password('analytics-browser-passphrase'), email_verified_at=now())
    tenant = Tenant(id=uid(), name='Analytics Browser Workspace', public_key='pub_' + uid(),
                    admin_key_hash=hash_token(uid()))
    db.add_all([user, tenant])
    db.flush()
    db.add(Membership(user_id=user.id, tenant_id=tenant.id, role='viewer'))
    for channel, role, seconds in (('web', 'assistant', 10), ('whatsapp', 'agent', 30)):
        conv = Conversation(id=uid(), tenant_id=tenant.id, channel=channel, created_at=at,
                            handoff=role == 'agent', status='agent' if role == 'agent' else 'bot')
        db.add(conv)
        db.flush()
        db.add_all([Message(conversation_id=conv.id, role='user', content='Browser fixture question', created_at=at),
                    Message(conversation_id=conv.id, role=role, content='Browser fixture response',
                            delivery_status='sent', created_at=at + timedelta(seconds=seconds))])
        if channel == 'web':
            db.add(Lead(tenant_id=tenant.id, conversation_id=conv.id, fields={'name': 'Fixture lead'}, created_at=at))
    record(db, 'first_lead', tenant.id)
reconcile()
