"""UTC daily metrics and allowlisted, deduplicated product milestones."""
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from ..db import ProductEvent, Session, uid

EVENTS = {'signup_started', 'signup_completed', 'workspace_created', 'whatsapp_connected',
          'first_knowledge_ready', 'first_test_message', 'went_live', 'first_customer_conversation',
          'first_lead', 'upgrade_started', 'upgrade_completed', 'campaign_sent',
          'onboarding_whatsapp_completed', 'onboarding_knowledge_completed',
          'onboarding_test_completed', 'onboarding_live_completed'}


def record(db, name, tenant_id=None, key=None):
    if name not in EVENTS:
        raise ValueError('Unsupported product event')
    if tenant_id is None and name not in ('signup_started', 'signup_completed'):
        raise ValueError('Workspace required')
    db.execute(insert(ProductEvent).values(id=uid(), name=name, tenant_id=tenant_id,
                                           event_key=key or f'{tenant_id}:{name}')
               .on_conflict_do_nothing(index_elements=['event_key']))


def milestone(db, name, tenant_id, step=None):
    record(db, name, tenant_id)
    if step:
        record(db, f'onboarding_{step}_completed', tenant_id)


def signup_started():
    with Session.begin() as db:
        record(db, 'signup_started', key=uid())


ROLLUP = """
WITH incoming AS (
    SELECT c.id, c.tenant_id, c.channel, c.analytics_handoff,
        min(m.created_at) AS first_inbound
    FROM conversations c JOIN messages m ON m.conversation_id = c.id
    WHERE m.role = 'user' AND m.kind != 'note'
    GROUP BY c.id
), responses AS (
    SELECT i.*, min(coalesce(m.dispatch_started_at, m.created_at)) AS first_reply,
        coalesce(bool_or(m.role = 'assistant'), false) AS bot_reply,
        coalesce(bool_or(m.role = 'agent'), false) AS agent_reply
    FROM incoming i LEFT JOIN messages m ON m.conversation_id = i.id
        AND m.role IN ('assistant', 'agent') AND m.kind != 'note'
        AND coalesce(m.dispatch_started_at, m.created_at) >= i.first_inbound
        AND (m.delivery_status IS NULL OR m.delivery_status IN ('sent', 'delivered', 'read'))
    GROUP BY i.id, i.tenant_id, i.channel, i.analytics_handoff, i.first_inbound
), conversation_days AS (
    SELECT tenant_id, (first_inbound AT TIME ZONE 'UTC')::date AS day, channel,
        count(*) AS conversations,
        count(*) FILTER (WHERE bot_reply AND NOT agent_reply AND NOT analytics_handoff) AS bot_answered,
        count(*) FILTER (WHERE analytics_handoff OR agent_reply) AS handed_off,
        coalesce(round(sum(extract(epoch FROM first_reply - first_inbound) * 1000)), 0)::bigint AS response_ms,
        count(first_reply) AS response_count
    FROM responses GROUP BY tenant_id, day, channel
), message_days AS (
    SELECT c.tenant_id, (m.created_at AT TIME ZONE 'UTC')::date AS day, c.channel, count(*) AS messages
    FROM messages m JOIN conversations c ON c.id = m.conversation_id
    WHERE m.kind != 'note' AND m.role IN ('user', 'assistant', 'agent')
        AND (m.role = 'user' OR m.delivery_status IS NULL OR m.delivery_status IN ('sent', 'delivered', 'read'))
    GROUP BY c.tenant_id, day, c.channel
), lead_days AS (
    SELECT l.tenant_id, (l.created_at AT TIME ZONE 'UTC')::date AS day, c.channel, count(*) AS leads
    FROM leads l JOIN conversations c ON c.id = l.conversation_id AND c.tenant_id = l.tenant_id
    GROUP BY l.tenant_id, day, c.channel
), days AS (
    SELECT tenant_id, day, channel FROM conversation_days UNION
    SELECT tenant_id, day, channel FROM message_days UNION SELECT tenant_id, day, channel FROM lead_days
)
INSERT INTO analytics_daily
    (tenant_id, day, channel, conversations, messages, bot_answered, handed_off, leads,
     response_ms, response_count, updated_at)
SELECT d.tenant_id, d.day, d.channel, coalesce(c.conversations, 0), coalesce(m.messages, 0),
    coalesce(c.bot_answered, 0), coalesce(c.handed_off, 0), coalesce(l.leads, 0),
    coalesce(c.response_ms, 0), coalesce(c.response_count, 0), CURRENT_TIMESTAMP
FROM days d LEFT JOIN conversation_days c USING (tenant_id, day, channel)
    LEFT JOIN message_days m USING (tenant_id, day, channel) LEFT JOIN lead_days l USING (tenant_id, day, channel)
"""


def reconcile():
    # One transaction makes rebuilding idempotent and removes stale rows after deletions.
    # Revisit historical cohorts because a later handoff changes their outcome.
    with Session.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('analytics-daily'))"))
        db.execute(text('DELETE FROM analytics_daily'))
        db.execute(text(ROLLUP))


if __name__ == '__main__':
    reconcile()
