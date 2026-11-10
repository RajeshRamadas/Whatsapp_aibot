"""Campaign browser accounts and a connected, isolated fake WhatsApp channel."""
from app.db import Channel, Membership, Session, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Campaign Browser Workspace', public_key='pub_' + uid(),
                    admin_key_hash=hash_token(uid()), whatsapp_phone_number_id='PNID_UI')
    db.add(tenant)
    db.flush()
    db.add(Channel(tenant_id=tenant.id, kind='whatsapp', external_id='PNID_UI', waba_id='123456'))
    for address, role in [('campaigns@example.com', 'owner'), ('campaign-reader@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Campaign Browser User', email_verified_at=now(),
                    password_hash=hash_password('campaigns-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
