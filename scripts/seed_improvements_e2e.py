"""Synthetic answer-review demo data; these questions are fixtures, not customer history."""
from app import rag
from app.db import Conversation, Membership, Session, Tenant, User, WidgetTurn, now, uid
from app.services.auth import hash_password, hash_token

with Session.begin() as db:
    tenant = Tenant(id=uid(), name='Answer Review Demo', public_key='pub_' + uid(), admin_key_hash=hash_token(uid()))
    db.add(tenant)
    db.flush()
    for address, role in [('improve@example.com', 'owner'), ('improve-reader@example.com', 'viewer')]:
        user = User(id=uid(), email=address, name='Review Demo User', email_verified_at=now(),
                    password_hash=hash_password('improve-browser-passphrase'))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))
    for question, answer, vote, sources in [
        ('What is the refund period?', 'Please ask our team.', 'down', []),
        ('what is the refund period?', 'I do not have that policy.', None, []),
        ('What are your hours?', 'Open weekdays, 9–5.', 'up', [{'number': 1, 'filename': 'Demo handbook'}]),
    ]:
        conversation = Conversation(tenant_id=tenant.id, channel='web')
        db.add(conversation)
        db.flush()
        turn = WidgetTurn(id=uid(), conversation_id=conversation.id, message=question, answer=answer,
                          sources=sources, state='complete', feedback=vote)
        db.add(turn)
        db.flush()
        rag.save_exchange_rows(db, conversation, [('user', question), ('assistant', answer)], sources=sources,
            review_answer=rag.Answer(answer, sources, question), widget_turn_id=turn.id)
