"""Rate limiting is shared across independent instances, not local process memory."""
import pytest
from redis.exceptions import ConnectionError

from app.rate_limits import RateLimiter


def test_two_instances_share_sliding_window():
    first = RateLimiter(2, window_seconds=10, namespace='shared-instance-test')
    second = RateLimiter(2, window_seconds=10, namespace='shared-instance-test')
    first.clear()
    assert first.allow('tenant-a:user-a')
    assert second.allow('tenant-a:user-a')
    assert not first.allow('tenant-a:user-a')
    assert second.allow('tenant-b:user-a')


def test_window_expires():
    import time
    limiter = RateLimiter(1, window_seconds=0.06, namespace='expiry-test')
    limiter.clear()
    assert limiter.allow('user')
    assert not limiter.allow('user')
    time.sleep(0.08)
    assert limiter.allow('user')


def test_unavailable_redis_does_not_allow_requests():
    class Offline:
        def eval(self, *_):
            raise ConnectionError('offline')

    limiter = RateLimiter(1, redis_client=Offline())
    with pytest.raises(ConnectionError):
        limiter.allow('user')
