"""The public branding answer carries the demo sign-ins only on a demo server (L5a)."""

from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.core.config import Settings
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.scope_harness import ADMIN_KEY, call, scoped_app
from tests.unit.test_public_branding import _factory

PASSWORD = "Nirmal-Clinic-Demo-2026"


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


def _demo(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "demo_mode": True,
        "admin_api_key": ADMIN_KEY,
        "llm_api_key": "k",
        "superadmin_email": "op@sahayak.example.com",
        "demo_owner_email": "owner@nirmalclinic.example.com",
        "demo_password": PASSWORD,
        "session_secret": "s" * 48,
        "email_provider": "smtp",
        "smtp_host": "smtp.example.com",
        "smtp_from": "Sahayak Studio <no-reply@sahayak.example.com>",
    }
    return Settings(**{**base, **over})


async def _branding(
    settings: Settings | None, stored: dict[str, Any] | None = None, redis: FakeRedis | None = None
) -> Any:
    with scoped_app() as (app, _):
        app.state.session_factory = _factory(stored or {})
        if settings is not None:
            app.state.settings = settings
        app.state.demo_redis = redis if redis is not None else FakeRedis()
        return await call(app, None, "GET", "/api/v1/public/branding")


async def test_demo_is_null_when_off() -> None:
    res = await _branding(None)
    assert res.status_code == 200
    assert res.json()["demo"] is None


async def test_demo_carries_both_sign_ins_when_on() -> None:
    res = await _branding(_demo(demo_reset_at_utc="20:15"))
    assert res.status_code == 200, res.text
    demo = res.json()["demo"]
    answers = demo.pop("answers")
    assert demo == {
        "logins": [
            {"role": "operator", "email": "op@sahayak.example.com", "password": PASSWORD},
            {"role": "owner", "email": "owner@nirmalclinic.example.com", "password": PASSWORD},
        ],
        "resets_at_utc": "20:15",
    }
    assert (answers["limit"], answers["left"], answers["per_visitor"]) == (300, 300, 15)
    assert answers["resets_at"].endswith("20:15:00Z")  # the day ends when the demo resets


async def test_the_numbers_move_with_the_answers_and_carry_no_visitor() -> None:
    from callobuzz_rag.demo import limits

    redis = FakeRedis()
    settings = _demo(demo_answers_per_day=10)
    lim = limits.limiter_from(redis, settings)
    for name in ("a", "b", "c", "d"):
        await lim.reserve(name)
    res = await _branding(settings, redis=redis)
    answers = res.json()["demo"]["answers"]
    assert (answers["limit"], answers["left"]) == (10, 6)
    assert set(answers) == {"limit", "left", "per_visitor", "resets_at"}  # numbers only
    assert res.headers["cache-control"] == "public, max-age=10"  # it moves, so not for a minute


async def test_the_numbers_are_null_when_redis_does_not_answer_and_the_page_still_loads() -> None:
    redis = FakeRedis()
    redis.down = True
    res = await _branding(_demo(), redis=redis)
    assert res.status_code == 200
    assert res.json()["demo"]["answers"] is None
    assert res.json()["demo"]["logins"]


async def test_default_reset_time_is_shown() -> None:
    res = await _branding(_demo())
    assert res.json()["demo"]["resets_at_utc"] == "21:30"


async def test_sign_up_is_never_available_on_a_demo_even_with_smtp_and_open_mode() -> None:
    res = await _branding(_demo(), {"signup_mode": "open"})
    body = res.json()
    assert body["accounts_enabled"] is True
    assert body["signup_available"] is False


async def test_only_the_demo_sign_in_passwords_are_public() -> None:
    res = await _branding(_demo(session_secret=SecretStr("s" * 48)))
    text = res.text
    assert ADMIN_KEY not in text and "s" * 48 not in text
