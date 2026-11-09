"""Shared Redis counters and read-only Prometheus collection across API/worker processes."""
import json
import logging
import time

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select

from .config import settings
from .db import Conversation, EmailOutbox, InboundEvent, Message, Session, Source, Tenant, UsageEvent, WidgetTurn, now
from .services.bot_policy import within_hours

KEY = 'chatbot:telemetry:v1'
BUCKETS = (.25, .5, 1, 2, 4, 8, 16, 32, 64, float('inf'))


def redis_client():
    return Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=.2, socket_timeout=.2)


def record(kind, name, outcome, duration):
    if kind not in ('http', 'integration', 'job', 'webhook'):
        raise ValueError('Unsupported telemetry kind')
    key = json.dumps([kind, name, outcome])
    try:
        with redis_client() as redis, redis.pipeline(transaction=True) as pipe:
            pipe.hsetnx(KEY, 'started', time.time())
            pipe.hincrby(KEY, key + ':count', 1)
            pipe.hincrbyfloat(KEY, key + ':sum', duration)
            for bound in BUCKETS:
                if duration <= bound:
                    pipe.hincrby(KEY, key + ':bucket:' + str(bound), 1)
            if outcome == 'success':
                pipe.hset(KEY, key + ':last', time.time())
            pipe.execute()
    except RedisError:
        logging.getLogger(__name__).warning('telemetry.unavailable')


class Collector:
    def collect(self):
        up = GaugeMetricFamily('chatbot_redis_up', 'Telemetry and queue Redis reachable')
        depth = GaugeMetricFamily('chatbot_queue_depth', 'Ready queued work', labels=['queue'])
        age = GaugeMetricFamily('chatbot_queue_oldest_seconds', 'Age of oldest ready queued work', labels=['queue'])
        try:
            with redis_client() as redis:
                redis.hsetnx(KEY, 'started', time.time())
                data = redis.hgetall(KEY)
                at = time.time() * 1000
                depth.add_metric(['arq'], redis.zcount('arq:queue', '-inf', at))
                oldest = redis.zrangebyscore('arq:queue', '-inf', at, start=0, num=1, withscores=True)
                age.add_metric(['arq'], max(0, (at - oldest[0][1]) / 1000) if oldest else 0)
            up.add_metric([], 1)
        except RedisError:
            data = {}
            up.add_metric([], 0)
        yield up
        for kind in ('http', 'integration', 'job', 'webhook'):
            whatsapp_seen = False
            calls = CounterMetricFamily(f'chatbot_{kind}_calls', 'Completed attempts', labels=['name', 'outcome'])
            duration = HistogramMetricFamily(f'chatbot_{kind}_duration_seconds', 'Attempt duration',
                                             labels=['name', 'outcome'])
            last = GaugeMetricFamily(f'chatbot_{kind}_last_success_timestamp_seconds', 'Last successful attempt',
                                     labels=['name'])
            for key in data:
                if not key.endswith(':count'):
                    continue
                prefix = key[:-6]
                metric_kind, name, outcome = json.loads(prefix)
                if metric_kind != kind:
                    continue
                calls.add_metric([name, outcome], float(data[key]))
                duration.add_metric([name, outcome], buckets=[
                    ('+Inf' if bound == float('inf') else str(bound),
                     float(data.get(prefix + ':bucket:' + str(bound), 0))) for bound in BUCKETS],
                    sum_value=float(data.get(prefix + ':sum', 0)))
                if prefix + ':last' in data:
                    last.add_metric([name], float(data[prefix + ':last']))
                    whatsapp_seen = whatsapp_seen or name == 'whatsapp'
            if kind == 'webhook' and not whatsapp_seen:
                last.add_metric(['whatsapp'], float(data.get('started', time.time())))
            yield calls
            yield duration
            yield last
        stamp = now()
        with Session() as db:
            queued, oldest = db.execute(select(func.count(), func.min(WidgetTurn.created_at))
                                        .where(WidgetTurn.state == 'queued')).one()
            depth.add_metric(['widget'], queued)
            age.add_metric(['widget'], max(0, (stamp - oldest).total_seconds()) if oldest else 0)
            queued, oldest = db.execute(select(func.count(), func.min(EmailOutbox.created_at)).where(
                EmailOutbox.status.in_(('queued', 'sending')), EmailOutbox.next_attempt_at <= stamp)).one()
            depth.add_metric(['email'], queued)
            age.add_metric(['email'], max(0, (stamp - oldest).total_seconds()) if oldest else 0)
            failed = GaugeMetricFamily('chatbot_failed_jobs', 'Durable terminal failures', labels=['kind'])
            for model, field, kind in ((Source, Source.status, 'ingestion'),
                                       (InboundEvent, InboundEvent.status, 'whatsapp'),
                                       (WidgetTurn, WidgetTurn.state, 'widget'),
                                       (EmailOutbox, EmailOutbox.status, 'email')):
                failed.add_metric([kind], db.scalar(select(func.count()).select_from(model).where(field == 'failed')))
            active = any(within_hours(tenant, stamp) for tenant in db.scalars(select(Tenant).where(
                Tenant.bot_live.is_(True), Tenant.suspended.is_(False), Tenant.whatsapp_phone_number_id.is_not(None))))
            hours = GaugeMetricFamily('chatbot_whatsapp_business_hours_active', 'Any live WhatsApp workspace open')
            hours.add_metric([], int(active))
            cost = GaugeMetricFamily('chatbot_ai_cost_usd_today', 'Known UTC daily AI cost', labels=['tenant_id'])
            unpriced = GaugeMetricFamily('chatbot_ai_unpriced_calls_today', 'Unpriced UTC daily calls',
                                          labels=['tenant_id'])
            start = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
            for tenant_id, amount in db.execute(select(UsageEvent.tenant_id, func.sum(UsageEvent.cost_usd)).where(
                    UsageEvent.created_at >= start).group_by(UsageEvent.tenant_id)):
                cost.add_metric([tenant_id], float(amount or 0))
            for tenant_id, count in db.execute(select(UsageEvent.tenant_id, func.count()).where(
                    UsageEvent.created_at >= start, UsageEvent.kind.in_(('chat', 'embedding')),
                    UsageEvent.cost_usd.is_(None)).group_by(UsageEvent.tenant_id)):
                unpriced.add_metric([tenant_id], count)
            messages = GaugeMetricFamily('chatbot_messages_today', 'Saved non-note messages today',
                                          labels=['tenant_id'])
            for tenant_id, count in db.execute(select(Conversation.tenant_id, func.count()).join(
                    Message, Message.conversation_id == Conversation.id).where(
                    Message.created_at >= start, Message.kind != 'note').group_by(Conversation.tenant_id)):
                messages.add_metric([tenant_id], count)
        for family in (depth, age, failed, hours, cost, unpriced, messages):
            yield family


def render():
    registry = CollectorRegistry()
    registry.register(Collector())
    return generate_latest(registry)
