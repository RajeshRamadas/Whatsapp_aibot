from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import insert

from app.db import Conversation, Lead, Plan, Session, Tenant, UsageEvent, now
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

with Session.begin() as db:
    plans = [
        {'id': 'starter', 'limits': {'ai_messages': 10, 'price_inr': 1999, 'seats': 2, 'files': 50, 'overage_inr': 1}},
        {'id': 'growth', 'limits': {'ai_messages': 50, 'price_inr': 5999, 'seats': 5, 'files': 500, 'overage_inr': 1}},
    ]
    statement = insert(Plan).values(plans)
    db.execute(statement.on_conflict_do_update(index_elements=['id'], set_={'limits': statement.excluded.limits}))
    db.add_all(UsageEvent(tenant_id=tenant_id, kind='answer', request_key=f'billing-fixture:{index}')
               for index in range(8))
