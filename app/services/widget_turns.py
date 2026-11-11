"""Claim and persist widget turns without holding a DB connection during AI calls."""
import logging
from datetime import timedelta

from sqlalchemy import or_, select

from .. import rag
from ..db import Conversation, Session, Tenant, WidgetTurn, now
from .customer_languages import copy_for
from .events import publish_sync
from .http_action_chat import redact_command
from .leads import notify_created
from .usage import QUOTA_NOTICE, blocked_notice, reserve

log = logging.getLogger(__name__)
HANDOFF = 'Your message is with our team. Keep this chat open to see their reply.'


def process_turn() -> bool:
    with Session.begin() as db:
        turn = db.scalar(select(WidgetTurn).where(or_(
            WidgetTurn.state == 'queued',
            (WidgetTurn.state == 'processing') & (WidgetTurn.claimed_at < now() - timedelta(minutes=10)),
        )).order_by(WidgetTurn.created_at, WidgetTurn.id).limit(1).with_for_update(skip_locked=True))
        if turn is None:
            return False
        if turn.attempts >= 3:
            turn.state = 'failed'
            return True
        turn.state, turn.claimed_at = 'processing', now()
        turn.attempts += 1
        conversation = db.get(Conversation, turn.conversation_id)
        tenant = db.get(Tenant, conversation.tenant_id)
        if tenant.suspended:
            turn.state = 'failed'
            return True
        attempt = turn.attempts

    try:
        wants_ai = not (turn.handoff or conversation.handoff or conversation.status == 'closed')
        quota_reached = wants_ai and not reserve(tenant.id, 'widget:' + turn.id)
        copy = copy_for(tenant, turn.message)
        notice = copy['widget_handoff']
        if quota_reached:
            notice = copy['quota'] if blocked_notice(tenant.id) == QUOTA_NOTICE else copy['billing']
        result = (rag.answer(tenant, conversation.id, turn.message, action_turn_key='widget:' + turn.id)
                  if wants_ai and not quota_reached else None)
    except Exception:
        log.warning('Widget answer generation failed')
        with Session.begin() as db:
            current = db.get(WidgetTurn, turn.id, with_for_update=True)
            if current and current.state == 'processing' and current.attempts == attempt:
                current.state = 'failed'
                current.message = redact_command(current.message)
        return True

    with Session.begin() as db:
        current = db.get(WidgetTurn, turn.id, with_for_update=True)
        if current is None or current.state != 'processing' or current.attempts != attempt:
            return True
        if db.get(Tenant, tenant.id).suspended:
            current.state = 'failed'
            return True
        conv = db.get(Conversation, conversation.id, with_for_update=True)
        current.message = redact_command(current.message)
        if result and result.action_failed:
            conv.handoff, conv.status = True, 'waiting'
            current.answer, current.sources, lead_fields = result.text, [], None
        elif turn.handoff or result is None or conv.handoff or conv.status == 'closed':
            if conv.status == 'bot':
                conv.handoff, conv.status = True, 'waiting'
            current.answer = notice
            current.sources, lead_fields = [], None
        else:
            current.answer, current.sources, lead_fields = result.text, result.sources, result.lead_fields
        current.state = 'complete'
        ai_answer = result if not (turn.handoff or conv.handoff or conv.status == 'closed') else None
        pairs = [('user', turn.message), ('assistant', current.answer)]
        created = rag.save_exchange_rows(db, conv, pairs, sources=current.sources, lead_fields=lead_fields,
                                         review_answer=ai_answer, widget_turn_id=turn.id)
    if created:
        notify_created(tenant.id, conversation.id)
    publish_sync(tenant.id, 'message.created', {'conversation_id': conversation.id})
    publish_sync(tenant.id, 'conversation.updated', {'conversation_id': conversation.id})
    return True
