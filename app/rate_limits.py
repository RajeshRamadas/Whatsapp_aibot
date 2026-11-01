"""Atomic, cross-process Redis sliding-window rate limits.

Fail closed if Redis is unavailable: an outage must not disable abuse controls.
"""
import hashlib
import secrets

from redis import Redis

from .config import settings

# Redis TIME is used to avoid clock skew across API processes.
_SLIDING_WINDOW = """
local now = redis.call('TIME')
local current = now[1] * 1000000 + now[2]
local cutoff = current - tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[2]) then
    redis.call('PEXPIRE', KEYS[1], math.ceil(tonumber(ARGV[1]) / 1000))
    return 0
end
redis.call('ZADD', KEYS[1], current, ARGV[3])
redis.call('PEXPIRE', KEYS[1], math.ceil(tonumber(ARGV[1]) / 1000))
return 1
"""


class RateLimiter:
    """Synchronous interface shared by API and worker call sites."""

    def __init__(self, limit: int, window_seconds: float = 60, namespace: str = 'chat',
                 redis_client: Redis | None = None):
        if limit < 1 or window_seconds <= 0:
            raise ValueError('Rate limit and window must be positive')
        self.limit = limit
        self.window = window_seconds
        self.namespace = namespace
        self.client = redis_client or Redis.from_url(settings.redis_url, socket_connect_timeout=2,
                                                    socket_timeout=2)

    def allow(self, key: str) -> bool:
        # Hash keys: do not expose email addresses or WhatsApp numbers in Redis keys.
        digest = hashlib.sha256(key.encode()).hexdigest()
        redis_key = f'ratelimit:{self.namespace}:{digest}'
        return bool(self.client.eval(_SLIDING_WINDOW, 1, redis_key,
                                     int(self.window * 1_000_000), self.limit,
                                     secrets.token_hex(16)))

    def clear(self) -> None:
        """Test helper; only delete this limiter's keys."""
        cursor = 0
        while True:
            cursor, keys = self.client.scan(cursor=cursor, match=f'ratelimit:{self.namespace}:*', count=100)
            if keys:
                self.client.delete(*keys)
            if cursor == 0:
                break
