"""Seed an isolated salon and an approved WhatsApp template for browser tests."""
from app.db import Channel, Membership, Session, Template, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(name='Salon Follow-ups', public_key='pub_' + uid(), admin_key_hash=hash_token(uid()),
                    whatsapp_phone_number_id='PNID_FOLLOW_UPS')
    user = User(id=uid(), email='follow-ups@example.com', name='Salon Owner', email_verified_at=now(),
                password_hash=hash_password('follow-up-browser-passphrase'))
    db.add_all([tenant, user])
    db.flush()
    channel = Channel(tenant_id=tenant.id, kind='whatsapp', external_id='PNID_FOLLOW_UPS', waba_id='921456')
    db.add_all([channel, Membership(user_id=user.id, tenant_id=tenant.id, role='owner')])
    db.flush()
    db.add(Template(channel_id=channel.id, meta_id='follow-up-approval', name='salon_follow_up', language='en_US',
        category='MARKETING', status='APPROVED', body='Need help booking at {{1}}?'))
