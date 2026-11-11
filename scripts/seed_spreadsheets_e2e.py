"""An older synthetic CSV, ready for an owner to reprocess into exact tables."""
from app.db import Membership, Session, Source, Tenant, User, now, uid
from app.services.auth import hash_password, hash_token
from app.services.storage import storage

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Spreadsheet Demo', public_key='pub_spreadsheets_demo',
        admin_key_hash=hash_token(uid()), allowed_origins='["http://localhost:3000"]', bot_live=True)
    db.add(tenant)
    db.flush()
    for address, role in [('spreadsheets@example.com', 'owner'), ('sheets-viewer@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Spreadsheet Demo User', email_verified_at=now(),
                    password_hash=hash_password('spreadsheets-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
    identity = uid()
    key = f'{tenant.id}/{identity}.csv'
    storage().put(key, b'Kind,Price\n2BHK,3500000\n2BHK,3900000\n2BHK,4500000\n3BHK,2000000\n')
    db.add(Source(id=identity, tenant_id=tenant.id, filename='plots.csv', kind='csv', status='ready',
                  file_path='storage:' + key))
