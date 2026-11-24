import time

from app.core.ratelimit import RateLimiter


def test_rate_limiter_forgets_idle_keys() -> None:
    limiter = RateLimiter()
    limiter.hit("login:1.2.3.4:a@x", 5, 60)
    limiter.hit("send:1", 5, 1)
    limiter.prune(now=time.monotonic() + 2)
    assert len(limiter) == 1  # the 1 s window is over, the 60 s one is not
    limiter.prune(now=time.monotonic() + 61)
    assert len(limiter) == 0
