"""accounts/ratelimit.py: fail-closed limits and the login lockout (accounts A3)."""

import pytest

from callobuzz_rag.accounts.ratelimit import AccountRateLimiter, lock_seconds
from callobuzz_rag.api.errors import APIError
from tests.unit.accounts_fakes import FakeRedis

EMAIL = "asha@shree.example"


def _limiter() -> tuple[AccountRateLimiter, FakeRedis]:
    redis = FakeRedis()
    return AccountRateLimiter(client=redis), redis


async def test_fixed_window() -> None:
    limiter, redis = _limiter()
    for _ in range(3):
        await limiter.hit("login_ip", "203.0.113.9", 3, 60)
    with pytest.raises(APIError) as exc:
        await limiter.hit("login_ip", "203.0.113.9", 3, 60)
    assert exc.value.status_code == 429 and exc.value.code == "rate_limited"
    assert exc.value.headers is not None and 1 <= int(exc.value.headers["Retry-After"]) <= 60
    await limiter.hit("login_ip", "198.51.100.1", 3, 60)  # another identity
    redis.advance(61)
    await limiter.hit("login_ip", "203.0.113.9", 3, 60)  # a new window


def test_lockout_backoff_schedule() -> None:
    assert [lock_seconds(n) for n in range(1, 11)] == [0, 0, 0, 0, 60, 120, 240, 480, 900, 900]


async def test_fifth_failure_locks_and_the_lock_grows() -> None:
    limiter, redis = _limiter()
    for n in range(1, 5):
        assert await limiter.record_failure(EMAIL) == n
        await limiter.check_lock(EMAIL)  # not locked yet
    assert await limiter.record_failure(EMAIL) == 5
    with pytest.raises(APIError) as exc:
        await limiter.check_lock(EMAIL)
    assert exc.value.status_code == 429 and exc.value.code == "account_locked"
    assert exc.value.headers == {"Retry-After": "60"}
    redis.advance(61)
    await limiter.check_lock(EMAIL)  # the lock ran out
    assert await limiter.record_failure(EMAIL) == 6
    with pytest.raises(APIError) as exc:
        await limiter.check_lock(EMAIL)
    assert exc.value.headers == {"Retry-After": "120"}


async def test_lock_applies_to_every_spelling_of_the_address() -> None:
    limiter, _ = _limiter()
    for _ in range(5):
        await limiter.record_failure(EMAIL)
    with pytest.raises(APIError):
        await limiter.check_lock(" ASHA@Shree.Example ")


async def test_success_clears_failures_and_lock() -> None:
    limiter, _ = _limiter()
    for _ in range(5):
        await limiter.record_failure(EMAIL)
    await limiter.clear_failures(EMAIL)
    await limiter.check_lock(EMAIL)
    assert await limiter.record_failure(EMAIL) == 1


async def test_failures_expire_after_fifteen_minutes() -> None:
    limiter, redis = _limiter()
    for _ in range(4):
        await limiter.record_failure(EMAIL)
    redis.advance(901)
    assert await limiter.record_failure(EMAIL) == 1


async def test_keys_hold_no_raw_email_or_ip() -> None:
    limiter, redis = _limiter()
    await limiter.hit("login_email", EMAIL, 10, 60)
    await limiter.hit("login_ip", "203.0.113.9", 10, 60)
    for _ in range(5):
        await limiter.record_failure(EMAIL)
    assert redis.values
    for key in redis.values:
        assert "asha" not in key and "shree" not in key and "203.0.113" not in key
        assert key.startswith(("acct:rl:", "acct:fail:", "acct:lock:"))


@pytest.mark.parametrize(
    "call",
    [
        lambda lim: lim.hit("login_ip", "1.2.3.4", 10, 60),
        lambda lim: lim.check_lock(EMAIL),
        lambda lim: lim.record_failure(EMAIL),
        lambda lim: lim.clear_failures(EMAIL),
    ],
)
async def test_redis_down_fails_closed(call) -> None:  # type: ignore[no-untyped-def]
    limiter, redis = _limiter()
    redis.down = True
    with pytest.raises(APIError) as exc:
        await call(limiter)
    assert exc.value.status_code == 503 and exc.value.code == "rate_limiter_unavailable"
