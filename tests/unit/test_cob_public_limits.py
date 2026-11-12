"""The public COB's limits: per client IP per hour and per engine per UTC day, fail closed."""

from datetime import UTC, datetime

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.cob.public import PublicCobLimiter, seconds_to_utc_midnight
from tests.unit.accounts_fakes import FakeRedis

NOW = datetime(2026, 10, 4, 10, 30, 15, tzinfo=UTC)


async def test_per_ip_hourly_limit() -> None:
    lim = PublicCobLimiter(FakeRedis(), per_ip_per_hour=2, daily=100)
    assert (await lim.hit("203.0.113.9", NOW)).allowed
    assert (await lim.hit("203.0.113.9", NOW)).allowed
    r = await lim.hit("203.0.113.9", NOW)
    assert not r.allowed and r.scope == "ip" and 0 < r.retry_after <= 3600
    assert (await lim.hit("198.51.100.7", NOW)).allowed  # another visitor is unaffected


async def test_the_next_hour_starts_a_new_window() -> None:
    lim = PublicCobLimiter(FakeRedis(), per_ip_per_hour=1, daily=100)
    assert (await lim.hit("a", NOW)).allowed
    assert not (await lim.hit("a", NOW)).allowed
    assert (await lim.hit("a", NOW.replace(hour=11))).allowed


async def test_daily_engine_limit_counts_every_ip() -> None:
    lim = PublicCobLimiter(FakeRedis(), per_ip_per_hour=100, daily=3)
    for ip in ("a", "b", "c"):
        assert (await lim.hit(ip, NOW)).allowed
    r = await lim.hit("d", NOW)
    assert not r.allowed and r.scope == "daily" and r.retry_after == seconds_to_utc_midnight(NOW)
    assert r.retry_after == 13 * 3600 + 29 * 60 + 45


async def test_a_refused_message_does_not_use_up_the_day() -> None:
    redis = FakeRedis()
    lim = PublicCobLimiter(redis, per_ip_per_hour=1, daily=2)
    assert (await lim.hit("a", NOW)).allowed
    assert not (await lim.hit("a", NOW)).allowed
    assert (await lim.hit("b", NOW)).allowed  # the refused one did not take the second slot


async def test_redis_failure_fails_closed() -> None:
    redis = FakeRedis()
    redis.down = True
    with pytest.raises(APIError) as e:
        await PublicCobLimiter(redis, per_ip_per_hour=5, daily=5).hit("a", NOW)
    assert e.value.status_code == 503 and e.value.code == "cob_public_unavailable"


async def test_ip_is_hashed_in_keys() -> None:
    redis = FakeRedis()
    await PublicCobLimiter(redis, per_ip_per_hour=5, daily=5).hit("203.0.113.9", NOW)
    assert redis.values and not any("203.0.113.9" in k for k in redis.values)
    assert any(k.startswith("cobpub:ip:") and k.endswith(":2026100410") for k in redis.values)
    assert "cobpub:day:20261004" in redis.values
