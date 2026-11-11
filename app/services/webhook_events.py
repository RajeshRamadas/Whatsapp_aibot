"""Capture ORM transitions after flush; Core-SQL producers stage explicitly.

The outbox shares the source transaction, including savepoint rollback. Message
ownership is routed from its persisted parent FK, as in the background dispatcher.
"""
from sqlalchemy import event, inspect, select

from ..db import Campaign, CampaignRecipient, Conversation, Message, Session, uid
from ..models.appointments import Booking
from ..models.payments import PaymentRequest

SUCCESS = frozenset(['sent', 'delivered', 'read'])


def before(row, name, default=None):
    history = inspect(row).attrs[name].history
    return history.deleted[0] if history.deleted else default if row in inspect(row).session.new else getattr(row, name)


@event.listens_for(Session, 'before_flush')
def capture(db, context, instances):
    pending = []
    for row in db.new.union(db.dirty):
        fresh = row in db.new
        if isinstance(row, Message):
            if fresh or inspect(row).attrs.delivery_status.history.has_changes():
                pending.append((row, 'message', fresh, before(row, 'delivery_status')))
        elif isinstance(row, Conversation):
            old_status, old_handoff = before(row, 'status', 'bot'), before(row, 'handoff', False)
            if row.status == 'closed' and (fresh or old_status != 'closed'):
                pending.append((row, 'conversation.closed', False, None))
            elif (row.status != 'closed' and (row.handoff or row.status in ('agent', 'waiting'))
                  and (fresh or not (old_handoff or old_status in ('agent', 'waiting')))):
                pending.append((row, 'conversation.handoff', False, None))
        elif isinstance(row, Booking) and row.state == 'confirmed' and before(row, 'state') != 'confirmed':
            pending.append((row, 'booking.created', False, None))
        elif isinstance(row, PaymentRequest) and row.state == 'paid' and before(row, 'state') != 'paid':
            pending.append((row, 'payment.completed', False, None))
        elif isinstance(row, Campaign) and row.state == 'completed' and before(row, 'state') != 'completed':
            pending.append((row, 'campaign.completed', False, None))
        elif isinstance(row, CampaignRecipient) and row.state in SUCCESS and before(row, 'state') not in SUCCESS:
            pending.append((row, 'campaign.message', False, None))
    db.info['_outgoing_transitions'] = pending


@event.listens_for(Session, 'after_flush_postexec')
def emit(db, context):
    from .outgoing_webhooks import stage
    for row, kind, fresh, old in db.info.pop('_outgoing_transitions', []):
        if kind == 'message':
            if row.kind == 'note' or row.role not in ('user', 'assistant', 'agent'):
                continue
            parent = db.execute(select(Conversation.tenant_id, Conversation.channel).join(
                Message, Message.conversation_id == Conversation.id).where(Message.id == row.id)).first()
            if not parent:
                continue
            if fresh and row.role == 'user':
                kind = 'message.received'
            elif (row.role != 'user' and old not in SUCCESS and (row.delivery_status in SUCCESS or (
                    fresh and parent.channel == 'web' and row.delivery_status is None))):
                kind = 'message.sent'
            else:
                continue
            stage(db, parent.tenant_id, kind, f'{kind}:{row.id}',
                  {'message_id': row.id, 'conversation_id': row.conversation_id,
                   'channel': parent.channel, 'kind': row.kind})
            continue
        if kind == 'campaign.message':
            stage(db, row.tenant_id, 'message.sent', 'campaign.message:' + row.id,
                  {'campaign_id': row.campaign_id, 'campaign_recipient_id': row.id,
                   'channel': 'whatsapp', 'is_test': row.is_test})
            continue
        name = ('conversation_id' if isinstance(row, Conversation) else 'booking_id' if isinstance(row, Booking)
                else 'payment_request_id' if isinstance(row, PaymentRequest) else 'campaign_id')
        data = {name: row.id, 'state': row.status if isinstance(row, Conversation) else row.state}
        if isinstance(row, (Booking, PaymentRequest)):
            data['conversation_id'] = row.conversation_id
        key = f'{kind}:{row.id}' + (':' + uid() if isinstance(row, Conversation) else '')
        stage(db, row.tenant_id, kind, key, data)


@event.listens_for(Session, 'after_rollback')
def clear(db):
    db.info.pop('_outgoing_transitions', None)
