"""Workspace-scoped, replayable live events backed by Redis Streams.

A capped stream holds the last 100 events for at most 10 minutes; XREAD blocks
on the same stream for live delivery, so no subscribe/replay race can lose events.
"""
import json
import logging
import re

from redis import Redis as SyncRedis
from redis.asyncio import Redis
from redis.exceptions import RedisError

from ..config import settings

log = logging.getLogger(__name__)
STREAM_MAX = 100
STREAM_TTL = 600
VALID_ID = re.compile(r'^[0-9]+-[0-9]+$')
EVENT_TYPES = {'conversation.updated', 'message.created', 'message.updated',
               'source.updated', 'lead.created', 'typing.updated', 'campaign.updated'}


def stream_key(tenant_id: str) -> str:
    return f'tenant:{tenant_id}:events'


def channel_key(tenant_id: str) -> str:
    return f'tenant:{tenant_id}'


def redis_client() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def publish(tenant_id: str, event: str, data: dict) -> str:
    """Persist before broadcasting. Payloads contain IDs/status only, never message content."""
    if event not in EVENT_TYPES:
        raise ValueError('Unsupported live event')
    if not tenant_id:
        raise ValueError('Workspace required')
    redis = redis_client()
    try:
        key = stream_key(tenant_id)
        # A transaction ensures that stream retention and notification stay ordered.
        async with redis.pipeline(transaction=True) as pipe:
            pipe.xadd(key, {'event': event, 'data': json.dumps(data)}, maxlen=STREAM_MAX)
            pipe.expire(key, STREAM_TTL)
            result = await pipe.execute()
        event_id = result[0]
        await redis.publish(channel_key(tenant_id), json.dumps({'id': event_id, 'event': event, 'data': data}))
        return event_id
    finally:
        await redis.aclose()


def encode_event(event_id: str, event: str, data: str) -> str:
    return f'id: {event_id}\nevent: {event}\ndata: {data}\n\n'


async def listen(tenant_id: str, last_id: str | None, disconnected):
    """Yield SSE frames; send a heartbeat every 15 seconds when idle.

    No Last-Event-ID means start at the current tail (no historical replay).
    A reconnect resumes strictly after its last delivered stream ID.
    """
    redis = redis_client()
    key = stream_key(tenant_id)
    try:
        if last_id is None:
            latest = await redis.xrevrange(key, count=1)
            cursor = latest[0][0] if latest else '0-0'
        else:
            cursor = last_id
        while not await disconnected():
            rows = await redis.xread({key: cursor}, count=STREAM_MAX, block=15000)
            if not rows:
                yield ': heartbeat\n\n'
                continue
            for _, entries in rows:
                for event_id, fields in entries:
                    cursor = event_id
                    if fields.get('event') in EVENT_TYPES:
                        yield encode_event(event_id, fields['event'], fields['data'])
    finally:
        await redis.aclose()


def publish_sync(tenant_id: str, event: str, data: dict) -> None:
    """Best-effort notification after a committed synchronous write."""
    if event not in EVENT_TYPES:
        raise ValueError('Unsupported live event')
    redis = SyncRedis.from_url(settings.redis_url, decode_responses=True, socket_timeout=1)
    try:
        key = stream_key(tenant_id)
        with redis.pipeline(transaction=True) as pipe:
            pipe.xadd(key, {'event': event, 'data': json.dumps(data)}, maxlen=STREAM_MAX)
            pipe.expire(key, STREAM_TTL)
            result = pipe.execute()
        redis.publish(channel_key(tenant_id),
                      json.dumps({'id': result[0], 'event': event, 'data': data}))
    except RedisError:
        log.warning('Live event publish failed; committed write remains valid', exc_info=True)
    finally:
        redis.close()
