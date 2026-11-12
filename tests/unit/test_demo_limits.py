"""A demo's daily answer limits (`demo/limits.py`): per visitor, for the whole demo, the day that
starts at the reset time, the polite sentences, the keyed visitor hash, and the model-call hook."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from callobuzz_rag.core.exceptions import LLMError
from callobuzz_rag.demo import budget, limits
from callobuzz_rag.demo.day import day_key, next_boundary, when_text
from tests.unit.accounts_fakes import FakeRedis

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def limiter(redis: FakeRedis, *, total: int = 10, per_visitor: int = 3, reset_at: str = "21:30"):
    return limits.DemoLimiter(
        redis,
        answers_per_day=total,
        per_visitor=per_visitor,
        reset_at=reset_at,
        now_fn=lambda: NOW,
    )


@pytest.fixture(autouse=True)
def _no_scope() -> Any:
    limits.set_scope(None)
    yield
    limits.set_scope(None)


# ------------------------------------------------------------------ the day


def test_the_demo_day_starts_at_the_reset_time() -> None:
    before = datetime(2026, 10, 7, 21, 29, tzinfo=UTC)
    at = datetime(2026, 10, 7, 21, 30, tzinfo=UTC)
    assert day_key(before, "21:30").isoformat() == "2026-10-06"
    assert day_key(at, "21:30").isoformat() == "2026-10-07"
    assert next_boundary(before, "21:30") == at
    assert next_boundary(at, "21:30") == datetime(2026, 10, 8, 21, 30, tzinfo=UTC)


def test_a_reset_at_midnight_is_the_plain_utc_day() -> None:
    assert day_key(datetime(2026, 10, 7, 23, 59, tzinfo=UTC)).isoformat() == "2026-10-07"
    assert day_key(datetime(2026, 10, 8, 0, 0, tzinfo=UTC)).isoformat() == "2026-10-08"


def test_the_moment_is_told_in_utc_and_ist() -> None:
    assert when_text(datetime(2026, 10, 7, 21, 30, tzinfo=UTC)) == "21:30 UTC (03:00 IST)"
    assert when_text(datetime(2026, 10, 7, 0, 0, tzinfo=UTC)) == "00:00 UTC (05:30 IST)"


async def test_the_counters_start_again_at_the_reset_moment() -> None:
    redis = FakeRedis()
    clock = [datetime(2026, 10, 7, 21, 29, tzinfo=UTC)]
    lim = limits.DemoLimiter(
        redis, answers_per_day=1, per_visitor=1, reset_at="21:30", now_fn=lambda: clock[0]
    )
    await lim.reserve("v")
    with pytest.raises(limits.DemoLimitReached):
        await lim.reserve("v")
    clock[0] = datetime(2026, 10, 7, 21, 30, tzinfo=UTC)
    await lim.reserve("v")  # a new demo day


# ------------------------------------------------------------------ the limits


async def test_one_visitor_stops_at_their_limit_and_others_go_on() -> None:
    redis = FakeRedis()
    lim = limiter(redis)
    for _ in range(3):
        await lim.reserve("alice")
    with pytest.raises(limits.DemoLimitReached) as err:
        await lim.reserve("alice")
    assert err.value.kind == "visitor"
    await lim.reserve("bob")
    # the refused attempt gave its count back: 3 for alice + 1 for bob
    assert redis.values[lim.global_key()] == 4
    assert redis.values[lim.visitor_key("alice")] == 3


async def test_the_whole_demo_stops_at_its_limit_for_everyone() -> None:
    redis = FakeRedis()
    lim = limiter(redis, total=3, per_visitor=5)
    for name in ("a", "b", "c"):
        await lim.reserve(name)
    with pytest.raises(limits.DemoLimitReached) as err:
        await lim.reserve("d")
    assert err.value.kind == "global"
    assert redis.values[lim.global_key()] == 3
    assert redis.values.get(lim.visitor_key("d"), 0) == 0


async def test_keys_expire_so_nothing_lingers() -> None:
    redis = FakeRedis()
    lim = limiter(redis)
    await lim.reserve("alice")
    assert await redis.ttl(lim.global_key()) == limits.TTL_S
    assert await redis.ttl(lim.visitor_key("alice")) == limits.TTL_S


async def test_peek_reads_without_counting() -> None:
    redis = FakeRedis()
    lim = limiter(redis, total=10, per_visitor=1)
    assert (await lim.peek("alice")).kind == "ok"
    await lim.reserve("alice")
    standing = await lim.peek("alice")
    assert (standing.kind, standing.visitor_used, standing.answers_used) == ("visitor", 1, 1)
    assert (await lim.peek("bob")).kind == "ok"
    assert redis.values[lim.global_key()] == 1  # peeking added nothing


async def test_the_visitor_limit_is_named_before_the_demo_limit() -> None:
    redis = FakeRedis()
    lim = limiter(redis, total=1, per_visitor=1)
    await lim.reserve("alice")
    assert (await lim.peek("alice")).kind == "visitor"
    assert (await lim.peek("bob")).kind == "global"


async def test_it_fails_closed_when_redis_is_down() -> None:
    redis = FakeRedis()
    redis.down = True
    lim = limiter(redis)
    assert (await lim.peek("alice")).kind == "unavailable"
    with pytest.raises(budget.DemoBudgetUnavailable):
        await lim.reserve("alice")
    assert await lim.counts() is None


async def test_counts() -> None:
    redis = FakeRedis()
    lim = limiter(redis, total=10)
    assert await lim.counts() == (0, 10)
    await lim.reserve("a")
    await lim.reserve("b")
    assert await lim.counts() == (2, 10)


# ------------------------------------------------------------------ the sentences


def test_the_visitor_sentence_says_the_number_the_time_and_no_store_link() -> None:
    text = limits.notice(
        "visitor", visitor_limit=15, resets=datetime(2026, 10, 7, 21, 30, tzinfo=UTC)
    )
    assert "(15)" in text
    assert "21:30 UTC (03:00 IST)" in text
    assert "come back tomorrow" in text.lower() and "install your own copy" in text
    assert "http" not in text and "codecanyon" not in text.lower()


def test_the_demo_sentence_says_it_is_used_up_and_when_it_resets() -> None:
    text = limits.notice(
        "global", visitor_limit=15, resets=datetime(2026, 10, 7, 21, 30, tzinfo=UTC)
    )
    assert text.startswith("Today's demo answers are all used up.")
    assert "21:30 UTC (03:00 IST)" in text and "http" not in text


def test_the_unavailable_sentence_is_plain() -> None:
    text = limits.notice("unavailable", visitor_limit=15, resets=NOW)
    assert "right now" in text and "try again" in text.lower()


def test_polite_text_maps_every_demo_error_and_nothing_else() -> None:
    settings = _settings()
    scope = None
    assert limits.current_scope() is scope
    visitor = limits.polite_text(limits.DemoLimitReached("visitor"), settings)
    assert visitor is not None and "(15)" in visitor
    calls = limits.polite_text(budget.DemoBudgetExceeded("x"), settings)
    assert calls is not None and calls.startswith("Today's demo answers are all used up")
    down = limits.polite_text(budget.DemoBudgetUnavailable("x"), settings)
    assert down is not None and "right now" in down
    assert limits.polite_text(LLMError("a real failure"), settings) is None
    assert limits.error_code(budget.DemoBudgetUnavailable("x")) == "demo_unavailable"
    assert limits.error_code(limits.DemoLimitReached("global")) == "demo_limit"


# ------------------------------------------------------------------ the visitor


def test_the_visitor_is_a_keyed_hash_never_the_address() -> None:
    a = limits.visitor_hash("secret", "203.0.113.7", None)
    assert "203" not in a and len(a) == 32
    assert a == limits.visitor_hash("secret", "203.0.113.7", None)
    assert a != limits.visitor_hash("secret", "203.0.113.8", None)
    assert a != limits.visitor_hash("other", "203.0.113.7", None)
    assert a != limits.visitor_hash("secret", "203.0.113.7", "user-1")  # the sign-in counts too
    assert limits.visitor_hash("secret", "203.0.113.7", "user-1") != limits.visitor_hash(
        "secret", "203.0.113.7", "user-2"
    )


def _settings(**over: Any) -> Any:
    base: dict[str, Any] = {
        "demo_mode": True,
        "demo_answers_per_day": 300,
        "demo_answers_per_visitor_per_day": 15,
        "demo_reset_at_utc": "21:30",
        "session_secret": SecretStr("s1,s0"),
        "admin_api_key": SecretStr("admin-key"),
        "trust_proxy_headers": False,
        "trusted_proxy_hops": 1,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _request(
    *,
    ip: str = "203.0.113.7",
    headers: dict[str, str] | None = None,
    user: Any = None,
    admin: bool = False,
    auth_kind: str = "none",
) -> Any:
    return SimpleNamespace(
        headers=headers or {},
        client=SimpleNamespace(host=ip),
        state=SimpleNamespace(user=user, is_admin=admin, auth_kind=auth_kind),
        app=SimpleNamespace(state=SimpleNamespace(settings=None)),
    )


async def _check(request: Any, redis: Any, settings: Any) -> Any:
    """`limits.check` as the gate calls it: the settings are the serving app's."""
    request.app.state.settings = settings
    return await limits.check(request, redis, settings)


async def test_check_is_nothing_when_demo_mode_is_off() -> None:
    redis = FakeRedis()
    assert await _check(_request(), redis, _settings(demo_mode=False)) is None
    assert limits.current_scope() is None
    assert redis.values == {}


async def test_the_admin_key_is_never_limited_but_a_super_admin_session_is() -> None:
    redis = FakeRedis()
    key = _request(admin=True, auth_kind="api_key")
    assert await _check(key, redis, _settings()) is None
    session = _request(admin=True, auth_kind="session", user=SimpleNamespace(id="sa"))
    scope = await _check(session, redis, _settings())
    assert scope is not None and scope.standing.kind == "ok"


async def test_one_address_is_one_visitor_and_two_addresses_are_two() -> None:
    redis = FakeRedis()
    settings = _settings(demo_answers_per_visitor_per_day=1)
    first = await _check(_request(ip="198.51.100.1"), redis, settings)
    assert first is not None
    await first.count()
    again = await _check(_request(ip="198.51.100.1"), redis, settings)
    assert again is not None and again.standing.kind == "visitor"
    other = await _check(_request(ip="198.51.100.2"), redis, settings)
    assert other is not None and other.standing.kind == "ok"
    # no raw address is stored anywhere
    assert not any("198.51.100" in str(key) for key in redis.values)


async def test_the_address_behind_a_trusted_proxy_is_the_visitors_not_the_proxys() -> None:
    redis = FakeRedis()
    settings = _settings(trust_proxy_headers=True, demo_answers_per_visitor_per_day=1)
    behind = {"x-forwarded-for": "9.9.9.9, 192.0.2.50"}
    a = await _check(_request(ip="10.0.0.1", headers=behind), redis, settings)
    assert a is not None
    await a.count()
    # another visitor through the same proxy (the same peer) is not the first one
    other = {"x-forwarded-for": "9.9.9.9, 192.0.2.51"}
    b = await _check(_request(ip="10.0.0.1", headers=other), redis, settings)
    assert b is not None and b.standing.kind == "ok"
    # a caller cannot pick their own identity by writing the header's first entry
    forged = {"x-forwarded-for": "1.1.1.1, 192.0.2.50"}
    c = await _check(_request(ip="10.0.0.1", headers=forged), redis, settings)
    assert c is not None and c.standing.kind == "visitor"


async def test_the_signed_in_user_is_part_of_the_visitor() -> None:
    redis = FakeRedis()
    settings = _settings(demo_answers_per_visitor_per_day=1)
    one = await _check(_request(user=SimpleNamespace(id="u1")), redis, settings)
    assert one is not None
    await one.count()
    same = await _check(_request(user=SimpleNamespace(id="u1")), redis, settings)
    other = await _check(_request(user=SimpleNamespace(id="u2")), redis, settings)
    assert same is not None and same.standing.kind == "visitor"
    assert other is not None and other.standing.kind == "ok"


async def test_check_with_redis_down_is_unavailable() -> None:
    redis = FakeRedis()
    redis.down = True
    scope = await _check(_request(), redis, _settings())
    assert scope is not None and scope.standing.kind == "unavailable"
    assert "right now" in scope.text()


# ------------------------------------------------------------------ the model-call hook


def _scope(redis: FakeRedis, *, total: int = 10, per_visitor: int = 3) -> limits.DemoTurnScope:
    lim = limiter(redis, total=total, per_visitor=per_visitor)
    scope = limits.DemoTurnScope(lim, "v", limits.Standing("ok"))
    limits.set_scope(scope)
    return scope


@pytest.fixture
def demo_on(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    spent: list[str] = []

    class _B:
        async def spend(self) -> None:
            spent.append("call")

    monkeypatch.setattr(budget, "_budget", lambda: _B())
    monkeypatch.setattr(budget, "_demo_on", lambda: True)
    return spent


async def test_a_turn_with_many_model_calls_counts_one_answer(demo_on: list[str]) -> None:
    redis = FakeRedis()
    scope = _scope(redis)
    for _ in range(4):  # label, rewrite, answer, fact check
        await budget.check_model_call("openrouter", "openrouter/x")
    assert redis.values[scope.limiter.global_key()] == 1
    assert redis.values[scope.limiter.visitor_key("v")] == 1
    assert demo_on == ["call"] * 4  # the calls backstop still counts every call


async def test_a_turn_without_a_model_call_counts_nothing(demo_on: list[str]) -> None:
    redis = FakeRedis()
    _scope(redis)
    # small talk answered by rules, a cache hit, a request for a person, a pause: no call at all
    assert redis.values == {}


async def test_the_free_local_model_is_not_an_answer(demo_on: list[str]) -> None:
    redis = FakeRedis()
    _scope(redis)
    await budget.check_model_call("ollama_chat", "ollama_chat/qwen3:4b")
    assert redis.values == {} and demo_on == []


async def test_a_call_outside_any_turn_only_counts_against_the_backstop(
    demo_on: list[str],
) -> None:
    await budget.check_model_call("openrouter", "openrouter/x")  # setup generation, a rule hint
    assert demo_on == ["call"]


async def test_a_refused_answer_does_not_spend_a_call(demo_on: list[str]) -> None:
    redis = FakeRedis()
    _scope(redis, per_visitor=1)
    await budget.check_model_call("openrouter")
    other = _scope(redis, per_visitor=1)  # a second turn of the same visitor
    with pytest.raises(limits.DemoLimitReached) as err:
        await budget.check_model_call("openrouter")
    assert isinstance(err.value, budget.DemoBudgetExceeded) and isinstance(err.value, LLMError)
    assert not other.counted
    assert demo_on == ["call"]


async def test_redis_down_at_the_first_call_refuses_it(demo_on: list[str]) -> None:
    redis = FakeRedis()
    _scope(redis)
    redis.down = True
    with pytest.raises(budget.DemoBudgetUnavailable):
        await budget.check_model_call("openrouter")
    assert demo_on == []


async def test_nothing_changes_with_demo_mode_off(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    _scope(redis)
    monkeypatch.setattr(budget, "_demo_on", lambda: False)
    await budget.check_model_call("openrouter")
    assert redis.values == {}


async def test_the_status_numbers() -> None:
    from callobuzz_rag.demo import runtime

    redis = FakeRedis()
    lim = limits.DemoLimiter(redis, answers_per_day=300, per_visitor=15, reset_at="21:30")
    await lim.reserve("a")
    app = SimpleNamespace(
        state=SimpleNamespace(settings=_settings(demo_model_calls_per_day=1200), demo_redis=redis)
    )
    status = await runtime.read_status(app, now=NOW)
    assert (status.answers_limit, status.answers_per_visitor_limit) == (300, 15)
    assert status.model_calls_limit == 1200
    assert status.answers_today == 1
