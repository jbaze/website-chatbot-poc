from app.ratelimit import RateLimiter


def test_rate_limiter_sliding_window():
    rl = RateLimiter(limit=3, window_seconds=10)
    assert rl.allow("a", now=0)
    assert rl.allow("a", now=1)
    assert rl.allow("a", now=2)
    assert not rl.allow("a", now=3)
    assert rl.retry_after("a", now=3) >= 7
    # a different key is independent
    assert rl.allow("b", now=3)
    # the oldest hit expires after the window
    assert rl.allow("a", now=10.1)
    assert not rl.allow("a", now=10.2)
