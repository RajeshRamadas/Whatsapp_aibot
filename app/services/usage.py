"""Provider usage ledger, monthly reservations and daily rollups."""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Date, cast, func, select
from sqlalchemy.dialects.postgresql import insert

from ..config import settings
from ..db import Membership, Plan, Session, Tenant, UsageDaily, UsageEvent, User, now
from . import email
from .billing import BILLING_NOTICE, entitled

log = logging.getLogger(__name__)
workspace = ContextVar('metered_workspace', default=None)
QUOTA_NOTICE = 'Our automated assistant has reached its monthly limit. Our team can help.'
_RESERVE = """
local count = tonumber(ARGV[1])
local allowed = count < tonumber(ARGV[2]) or ARGV[3] == '1'
if allowed then count = count + 1 end
redis.call('SET', KEYS[1], count, 'EX', 3456000)
if allowed then return 1 else return 0 end
"""


@contextmanager
def meter(tenant_id):
    token = workspace.set(tenant_id)
    try:
        yield
    finally:
        workspace.reset(token)


def measure(response, provider, kind):
    usage = getattr(response, 'usage', None)
    if provider == 'openai':
        return getattr(usage, 'prompt_tokens', None), getattr(usage, 'completion_tokens', 0 if kind == 'embedding'
                                                              else None)
    if provider == 'anthropic':
        return getattr(usage, 'input_tokens', None), getattr(usage, 'output_tokens', None)
    usage = getattr(response, 'usage_metadata', None)
    if kind == 'embedding':
        count = getattr(usage, 'prompt_token_count', None)
        if count is None:
            counts = [getattr(getattr(row, 'statistics', None), 'token_count', None)
                      for row in (getattr(response, 'embeddings', None) or [])]
            if counts and all(value is not None for value in counts):
                count = sum(counts)
        return int(count) if count is not None else None, 0
    output = getattr(usage, 'candidates_token_count', None)
    thoughts = getattr(usage, 'thoughts_token_count', None)
    return getattr(usage, 'prompt_token_count', None), (output or 0) + (thoughts or 0) if usage else None


def record(provider, model, kind, response):
    tenant_id = workspace.get()
    if tenant_id is None:
        return
    requested_model = model
    model = getattr(response, 'model', None) or getattr(response, 'model_version', None) or model
    input_tokens, output_tokens = measure(response, provider, kind)
    prices = settings.ai_prices_usd
    rate = prices.get(f'{provider}:{model}', prices.get(f'{provider}:{requested_model}'))
    cost = None
    if rate is not None and input_tokens is not None and output_tokens is not None:
        cost = (input_tokens * rate[0] + output_tokens * rate[1]) / Decimal(1_000_000)
    with Session.begin() as db:
        db.add(UsageEvent(tenant_id=tenant_id, kind=kind, provider=provider, model=model,
                          input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost))


def call(provider, kind, operation, **kwargs):
    try:
        result = operation(**kwargs)
    except Exception:
        record(provider, kwargs['model'], kind, None)
        raise
    record(provider, kwargs['model'], kind, result)
    return result


def redis_client():
    return Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)


def month_start(at):
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def monthly_count(db, tenant_id, start):
    return db.scalar(select(func.count()).select_from(UsageEvent).where(
        UsageEvent.tenant_id == tenant_id, UsageEvent.kind == 'answer', UsageEvent.created_at >= start))


def cache_key(tenant_id, start):
    return f'usage:monthly:{tenant_id}:{start:%Y-%m}'


def blocked_notice(tenant_id):
    with Session() as db:
        tenant = db.get(Tenant, tenant_id)
        return QUOTA_NOTICE if entitled(db, tenant, now()) else BILLING_NOTICE


def reserve(tenant_id, request_key):
    """Reserve one AI turn and deduplicate retries using the durable ledger."""
    with Session.begin() as db:
        tenant = db.get(Tenant, tenant_id, with_for_update=True)
        if db.scalar(select(UsageEvent.id).where(
            UsageEvent.tenant_id == tenant_id, UsageEvent.request_key == request_key,
        )):
            return True
        at = now()
        if not entitled(db, tenant, at):
            return False
        start = month_start(at)
        limit = db.get(Plan, tenant.plan_id).limits['ai_messages']
        count = monthly_count(db, tenant_id, start)
        overage = tenant.quota_behavior == 'overage'
        try:
            with redis_client() as redis:
                allowed = bool(redis.eval(_RESERVE, 1, cache_key(tenant_id, start), count, limit, int(overage)))
        except RedisError:
            allowed = count < limit or overage
        if allowed:
            db.add(UsageEvent(tenant_id=tenant_id, kind='answer', request_key=request_key,
                              overage=count >= limit, created_at=at))
            count += 1
        for percent in (80, 100):
            if count * 100 < limit * percent:
                continue
            statement = insert(UsageEvent).values(
                tenant_id=tenant_id, kind=f'alert{percent}', request_key=f'alert:{start:%Y-%m}:{percent}',
                created_at=at,
            ).on_conflict_do_nothing(index_elements=['tenant_id', 'request_key'])
            db.execute(statement)
        return allowed


def reconcile():
    """Rebuild daily totals and repair the current month's Redis counters."""
    with Session.begin() as db:
        day = cast(func.timezone('UTC', UsageEvent.created_at), Date)
        answer = UsageEvent.kind == 'answer'
        call_event = UsageEvent.kind.in_(('chat', 'embedding'))
        columns = ['tenant_id', 'day', 'ai_messages', 'overage_messages', 'ai_calls', 'input_tokens',
                   'output_tokens', 'cost_usd', 'unpriced_calls']
        totals = select(
            UsageEvent.tenant_id, day, func.count().filter(answer),
            func.count().filter(answer & UsageEvent.overage), func.count().filter(call_event),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0), func.coalesce(func.sum(UsageEvent.output_tokens), 0),
            func.coalesce(func.sum(UsageEvent.cost_usd), 0), func.count().filter(
                call_event & UsageEvent.cost_usd.is_(None)),
        ).where(answer | call_event).group_by(UsageEvent.tenant_id, day)
        statement = insert(UsageDaily).from_select(columns, totals)
        db.execute(statement.on_conflict_do_update(
            index_elements=['tenant_id', 'day'],
            set_={column: statement.excluded[column] for column in columns[2:]},
        ))
        tenants = db.scalars(select(Tenant.id)).all()
    start = month_start(now())
    for tenant_id in tenants:
        with Session.begin() as db:
            if db.get(Tenant, tenant_id, with_for_update=True) is None:
                continue
            count = monthly_count(db, tenant_id, start)
            try:
                with redis_client() as redis:
                    redis.set(cache_key(tenant_id, start), count, ex=3456000)
            except RedisError:
                log.warning('Usage counter cache unavailable')
                return


def send_alerts():
    with Session() as db:
        rows = db.scalars(select(UsageEvent).where(
            UsageEvent.kind.in_(('alert80', 'alert100')), UsageEvent.notified_at.is_(None),
        ).order_by(UsageEvent.created_at, UsageEvent.id).limit(25)).all()
    for event in rows:
        with Session() as db:
            recipients = db.scalars(select(User.email).join(Membership, Membership.user_id == User.id).where(
                Membership.tenant_id == event.tenant_id, Membership.role == 'owner',
            )).all()
        try:
            for recipient in recipients:
                percent = event.kind.removeprefix('alert')
                email.sender.send(recipient, 'AI usage reached ' + percent + '%',
                                  'Open your workspace to review its AI usage and quota settings.')
        except Exception:
            log.warning('Usage alert delivery failed')
            continue
        with Session.begin() as db:
            current = db.get(UsageEvent, event.id)
            if current is not None:
                current.notified_at = now()
