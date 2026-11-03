from fastapi.testclient import TestClient

from app.db import Conversation, Lead, Session, Tenant, now
from app.main import app

credentials = {'email': 'inbox@example.com', 'password': 'inbox-test-passphrase-392'}
with TestClient(app, base_url='http://localhost') as client:
    client.post('/api/v1/auth/signup', json={**credentials, 'name': 'Inbox Agent'}).raise_for_status()
    client.post('/api/v1/auth/login', json=credentials).raise_for_status()
    response = client.post('/api/v1/workspaces', json={'name': 'Inbox Browser Test'},
                           headers={'X-CSRF-Token': client.cookies['chatbot_csrf']})
    response.raise_for_status()
    tenant_id = response.json()['tenant_id']

with Session.begin() as db:
    db.get(Tenant, tenant_id).whatsapp_phone_number_id = 'browser-fixture'
    db.add(Conversation(tenant_id=tenant_id, external_user='15550001234', channel='whatsapp',
                        last_inbound_at=now()))

with Session.begin() as db:
    conversation = Conversation(tenant_id=tenant_id, external_user='lead-browser-fixture', channel='web')
    db.add(conversation)
    db.flush()
    db.add(Lead(tenant_id=tenant_id, conversation_id=conversation.id,
                fields={'name': 'Asha Demo', 'phone': '+15550001235', 'need': 'Pricing'}))
