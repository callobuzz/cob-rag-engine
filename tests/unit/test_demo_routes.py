"""The demo routes, the nightly schedule and the command line (L5a)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.demo import __main__ as cli
from callobuzz_rag.demo import reset, runtime
from callobuzz_rag.demo.scheduler import Scheduler, latest_slot
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.scope_harness import ADMIN_KEY, call, scoped_app

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _demo_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "demo_mode": True,
        "admin_api_key": ADMIN_KEY,
        "llm_api_key": "k",
        "superadmin_email": "op@sahayak.example.com",
        "demo_owner_email": "owner@nirmalclinic.example.com",
        "demo_password": "Nirmal-Clinic-Demo-2026",
        "session_secret": "s" * 48,
    }
    return Settings(**{**base, **over})


# -------------------------------------------------------------------------------- routes


async def test_off_means_404_for_both_routes() -> None:
    with scoped_app() as (app, _):
        got = await call(app, "admin", "GET", "/api/v1/admin/demo")
        posted = await call(app, "admin", "POST", "/api/v1/admin/demo/reset")
    for reply in (got, posted):
        assert reply.status_code == 404 and reply.json()["code"] == "demo_mode_off"


async def test_status_when_on() -> None:
    redis = FakeRedis()
    with scoped_app() as (app, _):
        app.state.settings = _demo_settings()
        app.state.demo_redis = redis
        await redis.set(reset.LAST_RESET_KEY, "2026-10-07T03:00:00+00:00")
        await redis.set(
            reset.STATUS_KEY, json.dumps({"result": "failed", "failed_step": "widget_key"})
        )
        from callobuzz_rag.demo import limits
        from callobuzz_rag.demo.day import day_key

        today = day_key(datetime.now(UTC), app.state.settings.demo_reset_at_utc).isoformat()
        await redis.incr("demo:model_calls:" + today)
        await limits.limiter_from(redis, app.state.settings).reserve("someone")
        reply = await call(app, "admin", "GET", "/api/v1/admin/demo")
    body = reply.json()
    assert reply.status_code == 200
    assert body["enabled"] is True and body["running"] is False
    assert body["model_calls_limit"] == 1200 and body["model_calls_today"] == 1
    assert body["answers_limit"] == 300 and body["answers_per_visitor_limit"] == 15
    assert body["answers_today"] == 1
    assert body["last_reset_at"].startswith("2026-10-07T03:00:00")
    assert body["last_result"] == "failed" and body["last_failed_step"] == "widget_key"
    assert body["next_reset_at"] is not None


async def test_reset_starts_a_background_task(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []
    gate = asyncio.Event()

    async def fake_run(app: Any, trigger: str) -> reset.ResetReport:
        started.append(trigger)
        await gate.wait()
        return reset.ResetReport(trigger=trigger)

    monkeypatch.setattr(runtime, "run_for_app", fake_run)
    with scoped_app() as (app, _):
        app.state.settings = _demo_settings()
        app.state.demo_redis = FakeRedis()
        reply = await call(app, "admin", "POST", "/api/v1/admin/demo/reset")
        assert reply.status_code == 202 and reply.json()["running"] is True
        await asyncio.sleep(0)
        assert started == ["manual"]
        assert app.state.demo_reset_task is not None
        gate.set()
        await app.state.demo_reset_task


async def test_a_held_lock_is_a_409() -> None:
    redis = FakeRedis()
    with scoped_app() as (app, _):
        app.state.settings = _demo_settings()
        app.state.demo_redis = redis
        await redis.set(reset.LOCK_KEY, "someone", ex=1800, nx=True)
        reply = await call(app, "admin", "POST", "/api/v1/admin/demo/reset")
    assert reply.status_code == 409 and reply.json()["code"] == "demo_reset_running"


async def test_redis_down_is_a_503() -> None:
    redis = FakeRedis()
    redis.down = True
    with scoped_app() as (app, _):
        app.state.settings = _demo_settings()
        app.state.demo_redis = redis
        got = await call(app, "admin", "GET", "/api/v1/admin/demo")
        posted = await call(app, "admin", "POST", "/api/v1/admin/demo/reset")
    for reply in (got, posted):
        assert reply.status_code == 503 and reply.json()["code"] == "demo_unavailable"


@pytest.mark.parametrize("key", ["tk_a", "ck_a", "owner_a", "member_a"])
async def test_only_the_admin_principal_may_call_them(key: str) -> None:
    with scoped_app() as (app, _):
        app.state.settings = _demo_settings()
        app.state.demo_redis = FakeRedis()
        got = await call(app, key, "GET", "/api/v1/admin/demo")
        posted = await call(app, key, "POST", "/api/v1/admin/demo/reset")
    assert got.status_code == 403 and posted.status_code == 403


async def test_no_credential_is_a_401() -> None:
    with scoped_app() as (app, _):
        got = await call(app, None, "GET", "/api/v1/admin/demo")
    assert got.status_code == 401


def test_the_routes_are_classified_manage() -> None:
    from callobuzz_rag.api.permissions import ROUTE_OPS

    assert ROUTE_OPS[("GET", "/api/v1/admin/demo")] == "manage"
    assert ROUTE_OPS[("POST", "/api/v1/admin/demo/reset")] == "manage"


def test_the_guard_refuses_the_reset_for_a_session_but_not_the_admin_key() -> None:
    from callobuzz_rag.demo import guard

    assert guard.refusal("POST", "/api/v1/admin/demo/reset", admin_key=False) == "manage"
    assert guard.refusal("POST", "/api/v1/admin/demo/reset", admin_key=True) is None
    assert guard.refusal("GET", "/api/v1/admin/demo", admin_key=False) is None


# -------------------------------------------------------------------------------- scheduler


class _Runs:
    def __init__(self) -> None:
        self.triggers: list[str] = []
        self.error: Exception | None = None

    async def __call__(self, trigger: str) -> None:
        self.triggers.append(trigger)
        if self.error is not None:
            raise self.error


def _scheduler(redis: FakeRedis, runs: _Runs, started: datetime) -> Scheduler:
    return Scheduler(redis, runs, reset_at="21:30", started_at=started, start_delay_s=120)


async def test_runs_at_the_reset_time_and_once_only() -> None:
    redis, runs = FakeRedis(), _Runs()
    await redis.set(reset.LAST_RESET_KEY, NOW.isoformat())  # a good reset: no first seed
    s = _scheduler(redis, runs, NOW)
    await s.tick(NOW)
    await s.tick(datetime(2026, 10, 7, 21, 29, tzinfo=UTC))
    assert runs.triggers == []
    await s.tick(datetime(2026, 10, 7, 21, 30, 5, tzinfo=UTC))
    await s.tick(datetime(2026, 10, 7, 21, 31, 5, tzinfo=UTC))
    await s.tick(datetime(2026, 10, 7, 23, 0, tzinfo=UTC))
    assert runs.triggers == ["schedule"]
    await s.tick(datetime(2026, 10, 8, 21, 30, 1, tzinfo=UTC))
    assert runs.triggers == ["schedule", "schedule"]


async def test_a_late_tick_still_runs_the_night_once() -> None:
    redis, runs = FakeRedis(), _Runs()
    await redis.set(reset.LAST_RESET_KEY, NOW.isoformat())
    s = _scheduler(redis, runs, NOW)
    await s.tick(datetime(2026, 10, 7, 21, 29, 59, tzinfo=UTC))
    await s.tick(datetime(2026, 10, 7, 21, 31, 1, tzinfo=UTC))  # the 21:30 minute was skipped
    assert runs.triggers == ["schedule"]


async def test_two_processes_reset_once() -> None:
    redis = FakeRedis()
    await redis.set(reset.LAST_RESET_KEY, NOW.isoformat())
    first, second = _Runs(), _Runs()
    a, b = _scheduler(redis, first, NOW), _scheduler(redis, second, NOW)
    late = datetime(2026, 10, 7, 21, 30, 5, tzinfo=UTC)
    await a.tick(NOW)
    await b.tick(NOW)
    await a.tick(late)
    await b.tick(late)
    assert len(first.triggers) + len(second.triggers) == 1


async def test_a_server_started_after_the_reset_time_does_not_reset_at_once() -> None:
    redis, runs = FakeRedis(), _Runs()
    await redis.set(reset.LAST_RESET_KEY, NOW.isoformat())
    started = datetime(2026, 10, 7, 22, 0, tzinfo=UTC)
    s = _scheduler(redis, runs, started)
    await s.tick(started)
    await s.tick(started + timedelta(minutes=1))
    assert runs.triggers == []


async def test_first_seed_runs_120_seconds_after_start() -> None:
    redis, runs = FakeRedis(), _Runs()
    s = _scheduler(redis, runs, NOW)
    await s.tick(NOW)
    await s.tick(NOW + timedelta(seconds=60))
    assert runs.triggers == []
    await s.tick(NOW + timedelta(seconds=121))
    assert runs.triggers == ["start"]
    await s.tick(NOW + timedelta(seconds=300))
    assert runs.triggers == ["start"]


async def test_a_stale_reset_counts_as_missing() -> None:
    redis, runs = FakeRedis(), _Runs()
    await redis.set(reset.LAST_RESET_KEY, (NOW - timedelta(hours=26)).isoformat())
    s = _scheduler(redis, runs, NOW)
    await s.tick(NOW)
    await s.tick(NOW + timedelta(seconds=130))
    assert runs.triggers == ["start"]


async def test_another_process_seeding_first_cancels_the_start_run() -> None:
    redis, runs = FakeRedis(), _Runs()
    s = _scheduler(redis, runs, NOW)
    await s.tick(NOW)
    await redis.set(reset.LAST_RESET_KEY, (NOW + timedelta(seconds=90)).isoformat())
    await s.tick(NOW + timedelta(seconds=130))
    assert runs.triggers == []


async def test_errors_are_logged_never_raised(caplog: pytest.LogCaptureFixture) -> None:
    redis, runs = FakeRedis(), _Runs()
    runs.error = reset.ResetRunning("busy")
    s = _scheduler(redis, runs, NOW)
    await s.tick(NOW)
    await s.tick(NOW + timedelta(seconds=130))  # raises ResetRunning inside: swallowed
    redis.down = True
    await s.tick(NOW + timedelta(seconds=200))  # Redis down: swallowed
    runs.error = reset.ResetUnavailable("down")
    redis.down = False
    await _scheduler(redis, runs, NOW).tick(NOW)


def test_latest_slot() -> None:
    assert latest_slot("21:30", datetime(2026, 10, 7, 21, 30, tzinfo=UTC)) == datetime(
        2026, 10, 7, 21, 30, tzinfo=UTC
    )
    assert latest_slot("21:30", datetime(2026, 10, 7, 9, 0, tzinfo=UTC)) == datetime(
        2026, 10, 6, 21, 30, tzinfo=UTC
    )


# -------------------------------------------------------------------------------- command line


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def _nosleep(_: float) -> None:
    return None


STATUS_OK = {
    "enabled": True,
    "running": False,
    "last_reset_at": "2026-10-07T03:00:00+00:00",
    "last_result": "ok",
    "last_failed_step": None,
    "next_reset_at": "2026-10-07T21:30:00+00:00",
    "model_calls_today": 3,
    "model_calls_limit": 1200,
    "answers_today": 7,
    "answers_limit": 300,
    "answers_per_visitor_limit": 15,
}


async def test_cli_status(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=STATUS_OK)

    code = await cli.run(["status"], transport=_transport(handler), admin_key="adm_secret")
    out = capsys.readouterr().out
    assert (
        code == 0
        and "3 of 1200" in out
        and "7 of 300" in out
        and "visitor: 15" in out
        and "adm_secret" not in out
    )
    assert seen[0].headers["x-api-key"] == "adm_secret"


async def test_cli_status_off_is_exit_2() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "off", "code": "demo_mode_off"})

    assert await cli.run(["status"], transport=_transport(handler), admin_key="k") == 2
    assert await cli.run(["reset"], transport=_transport(handler), admin_key="k") == 2


async def test_cli_reset_wait_ok_and_failed() -> None:
    def make(final: dict[str, Any]) -> Any:
        polls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(202, json={**STATUS_OK, "running": True})
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(200, json={**STATUS_OK, "running": True})
            return httpx.Response(200, json=final)

        return handler

    ok = await cli.run(
        ["reset", "--wait"], transport=_transport(make(STATUS_OK)), sleep=_nosleep, admin_key="k"
    )
    failed = await cli.run(
        ["reset", "--wait"],
        transport=_transport(
            make({**STATUS_OK, "last_result": "failed", "last_failed_step": "plan"})
        ),
        sleep=_nosleep,
        admin_key="k",
    )
    assert ok == 0 and failed == 1


async def test_cli_reset_without_wait_returns_at_once() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(202, json={**STATUS_OK, "running": True})

    assert await cli.run(["reset"], transport=_transport(handler), admin_key="k") == 0
    assert calls == ["POST"]


async def test_cli_reports_a_refused_reset_and_an_unreachable_server() -> None:
    def busy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "A demo reset is already running.", "code": "x"})

    assert await cli.run(["reset"], transport=_transport(busy), admin_key="k") == 1

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no", request=request)

    assert await cli.run(["status"], transport=_transport(down), admin_key="k") == 1


async def test_cli_needs_an_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ADMIN_API_KEY", "")
    assert await cli.run(["status"], transport=_transport(lambda r: httpx.Response(200))) == 1


class _FailingRuns(_Runs):
    async def __call__(self, trigger: str) -> Any:
        self.triggers.append(trigger)
        return reset.ResetReport(trigger=trigger, result="failed", failed_step="wait")


async def test_a_failed_reset_is_retried_after_ten_minutes_at_most_three_times() -> None:
    redis, runs = FakeRedis(), _FailingRuns()
    await redis.set(reset.LAST_RESET_KEY, NOW.isoformat())  # a recent good one: not stale
    s = _scheduler(redis, runs, NOW)
    night = datetime(2026, 10, 7, 21, 30, 5, tzinfo=UTC)
    await s.tick(NOW)
    await s.tick(night)
    assert runs.triggers == ["schedule"]
    await s.tick(night + timedelta(minutes=9))
    assert runs.triggers == ["schedule"]
    await s.tick(night + timedelta(minutes=10, seconds=1))
    assert runs.triggers == ["schedule", "retry"]
    await s.tick(night + timedelta(minutes=21))
    await s.tick(night + timedelta(minutes=32))
    await s.tick(night + timedelta(minutes=60))
    await s.tick(night + timedelta(hours=3))
    assert runs.triggers == ["schedule", "retry", "retry", "retry"]


async def test_the_retry_count_restarts_with_the_next_slot() -> None:
    redis, runs = FakeRedis(), _FailingRuns()
    await redis.set(reset.LAST_RESET_KEY, NOW.isoformat())
    s = _scheduler(redis, runs, NOW)
    night = datetime(2026, 10, 7, 21, 30, 5, tzinfo=UTC)
    await s.tick(NOW)
    for minutes in (0, 11, 22, 33, 44):
        await s.tick(night + timedelta(minutes=minutes))
    assert runs.triggers.count("retry") == 3
    await s.tick(datetime(2026, 10, 8, 21, 30, 5, tzinfo=UTC))
    await s.tick(datetime(2026, 10, 8, 21, 41, 0, tzinfo=UTC))
    assert runs.triggers[-2:] == ["schedule", "retry"]


async def test_a_good_reset_plans_no_retry() -> None:
    redis, runs = FakeRedis(), _Runs()
    await redis.set(reset.LAST_RESET_KEY, NOW.isoformat())
    s = _scheduler(redis, runs, NOW)
    await s.tick(NOW)
    await s.tick(datetime(2026, 10, 7, 21, 30, 5, tzinfo=UTC))
    await s.tick(datetime(2026, 10, 7, 22, 30, tzinfo=UTC))
    assert runs.triggers == ["schedule"]
