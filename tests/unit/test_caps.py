"""The plan limits (accounts A6): the UTC month clock, effective limits, the question cap's
429 (API and widget bodies, admin bypass), and the knowledge-base and widget-key 409s."""

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.accounts import caps
from callobuzz_rag.accounts.settings_store import Branding, EngineSettings
from callobuzz_rag.api.errors import APIError
from tests.unit.scope_harness import call, scoped_app


@pytest.fixture(autouse=True)
def _no_pricing():  # type: ignore[no-untyped-def]
    """A workspace without pricing: the cap behaves exactly as before billing (BIL4)."""
    from callobuzz_rag.billing.pricing import PricingFields, PricingOverrides, combine

    eff = combine(PricingFields(), PricingOverrides(), currency="INR", exempt=False)
    with patch("callobuzz_rag.billing.pricing.effective_pricing", AsyncMock(return_value=eff)):
        yield


# ------------------------------------------------------------------- clock


@pytest.mark.parametrize(
    ("now", "month", "seconds"),
    [
        (datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC), date(2026, 1, 1), 1),
        (datetime(2028, 2, 29, 23, 59, 59, tzinfo=UTC), date(2028, 2, 1), 1),  # leap day
        (datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC), date(2026, 12, 1), 1),  # year end
        (datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC), date(2026, 9, 1), 30 * 86400),
        (datetime(2026, 9, 30, 23, 59, 58, 500000, tzinfo=UTC), date(2026, 9, 1), 2),
    ],
)
def test_month_clock(now: datetime, month: date, seconds: int) -> None:
    assert caps.current_month(now) == month
    assert caps.seconds_until_next_month(now) == seconds


def test_the_month_is_utc_whatever_the_caller_timezone() -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 10, 1, 1, 0, tzinfo=india)  # still 30 Sep in UTC
    assert caps.current_month(local) == date(2026, 9, 1)
    assert caps.next_month_start(local) == datetime(2026, 10, 1, tzinfo=UTC)
    assert caps.seconds_until_next_month(local) == 4 * 3600 + 30 * 60


# -------------------------------------------------------- effective limits


def _session_row(row: tuple[Any, ...] | None) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            (None, 3, 1000, 500, 2, 10, 200_000, True, {}, 0, 0, 0, 0, 0, 0),
            caps.Limits(3, 1000, 500, 2, 10, 200_000),
        ),  # the plan's
        (
            (25, 3, 1000, 500, 2, None, None, False, None, 0, 0, 0, 0, 0, 0),
            caps.Limits(25, 1000, 500, 2, count_cached_answers=False),
        ),  # the override wins
        ((None, None, None, None, None, None, None, True, {}, 0, 0, 0, 0, 0, 0), caps.Limits()),  # unlimited
        (None, caps.Limits()),  # no such tenant: nothing to enforce
    ],
)
async def test_effective_limits(row: Any, expected: caps.Limits) -> None:
    assert await caps.effective_limits(_session_row(row), uuid.uuid4()) == expected


async def test_question_quota_reads_limit_and_month_in_one_query() -> None:
    session = _session_row((1000, None, {}, 0))
    assert await caps.question_quota(session, uuid.uuid4(), date(2026, 9, 1)) == (1000, 0)
    assert session.execute.await_count == 1
    sql = str(session.execute.await_args.args[0])
    assert "LEFT OUTER JOIN tenant_question_counts" in sql and "JOIN plans" in sql
    assert await caps.question_quota(_session_row((None, 5, {}, 0)), uuid.uuid4(), date.today()) is None


async def test_a_locking_quota_read_takes_the_count_row_first() -> None:
    session = _session_row((1000, 999, {}, 0))
    assert await caps.question_quota(session, uuid.uuid4(), date(2026, 9, 1), lock=True) == (
        1000,
        999,
    )
    sql = [str(c.args[0]) for c in session.execute.await_args_list]
    assert len(sql) == 3
    assert "ON CONFLICT" in sql[0] and "tenant_question_counts" in sql[0]
    assert "FOR UPDATE" in sql[1] and "tenant_question_counts" in sql[1]


# ------------------------------------------------------------ question cap


def _request(*, admin: bool = False) -> Any:
    return SimpleNamespace(
        state=SimpleNamespace(is_admin=admin),
        app=SimpleNamespace(state=SimpleNamespace(session_factory=object())),
    )


def _quota(value: tuple[int, int] | None) -> Any:
    return patch.object(caps, "question_quota", AsyncMock(return_value=value))


def _branding(email: str | None) -> Any:
    engine = EngineSettings(branding=Branding(support_email=email))
    return patch(
        "callobuzz_rag.accounts.settings_store.get_engine_settings",
        AsyncMock(return_value=engine),
    )


NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


async def test_under_the_cap_passes() -> None:
    with _quota((1000, 999)):
        await caps.enforce_question_cap(AsyncMock(), _request(), uuid.uuid4(), now=NOW)


async def test_unlimited_passes() -> None:
    with _quota(None):
        await caps.enforce_question_cap(AsyncMock(), _request(), uuid.uuid4(), now=NOW)


async def test_at_the_cap_is_a_429_with_the_details() -> None:
    with (
        _quota((1000, 1000)),
        _branding("support@shree.example"),
        pytest.raises(APIError) as err,
    ):
        await caps.enforce_question_cap(AsyncMock(), _request(), uuid.uuid4(), now=NOW)
    exc = err.value
    assert exc.status_code == 429
    assert exc.code == "question_cap_reached"
    assert exc.detail == "Monthly question limit reached — contact support@shree.example"
    assert exc.extra == {"limit": 1000, "used": 1000, "resets_at": "2026-10-01T00:00:00Z"}
    assert exc.headers == {"Retry-After": str(12 * 3600)}


async def test_without_a_support_email_it_says_your_provider() -> None:
    with _quota((5, 7)), _branding(None), pytest.raises(APIError) as err:
        await caps.enforce_question_cap(AsyncMock(), _request(), uuid.uuid4(), now=NOW)
    assert err.value.detail == "Monthly question limit reached — contact your provider"
    assert err.value.extra["used"] == 7  # soft cap: what was in flight is reported as is


async def test_a_branding_failure_still_refuses() -> None:
    broken = patch(
        "callobuzz_rag.accounts.settings_store.get_engine_settings",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    with _quota((5, 5)), broken, pytest.raises(APIError) as err:
        await caps.enforce_question_cap(AsyncMock(), _request(), uuid.uuid4(), now=NOW)
    assert err.value.status_code == 429
    assert err.value.detail.endswith("contact your provider")


async def test_the_widget_body_is_polite_and_reveals_nothing() -> None:
    with _quota((5, 5)), _branding("support@shree.example"), pytest.raises(APIError) as err:
        await caps.enforce_question_cap(AsyncMock(), _request(), uuid.uuid4(), widget=True, now=NOW)
    exc = err.value
    assert exc.status_code == 429 and exc.code == "question_cap_reached"
    assert exc.detail == "This assistant is not available right now."
    assert exc.extra == {}
    assert exc.headers == {"Retry-After": str(12 * 3600)}


async def test_the_admin_is_never_capped() -> None:
    quota = AsyncMock(return_value=(1, 50))
    with patch.object(caps, "question_quota", quota):
        await caps.enforce_question_cap(AsyncMock(), _request(admin=True), uuid.uuid4())
    quota.assert_not_awaited()


# ------------------------------------------------------ knowledge bases


def _limits(**limits: int | None) -> Any:
    return patch.object(caps, "effective_limits", AsyncMock(return_value=caps.Limits(**limits)))


async def test_kb_room() -> None:
    tenant = SimpleNamespace(id=uuid.uuid4())
    with (
        _limits(max_knowledge_bases=3),
        patch.object(caps, "knowledge_base_count", AsyncMock(return_value=2)),
    ):
        await caps.check_kb_room(AsyncMock(), tenant)  # type: ignore[arg-type]
    with (
        _limits(max_knowledge_bases=3),
        patch.object(caps, "knowledge_base_count", AsyncMock(return_value=3)),
        pytest.raises(APIError) as err,
    ):
        await caps.check_kb_room(AsyncMock(), tenant)  # type: ignore[arg-type]
    assert err.value.status_code == 409
    assert err.value.code == "kb_limit_reached"
    assert err.value.detail == "Tenant corpus limit reached (3)"
    assert err.value.extra == {"limit": 3}


async def test_kb_create_at_the_cap_through_the_api() -> None:
    with (
        scoped_app() as (app, _),
        _limits(max_knowledge_bases=1),
        patch.object(caps, "knowledge_base_count", AsyncMock(return_value=1)),
    ):
        res = await call(app, "tk_a", "POST", "/api/v1/corpora", json={"name": "Second"})
    assert res.status_code == 409
    assert res.json() == {
        "detail": "Tenant corpus limit reached (1)",
        "code": "kb_limit_reached",
        "limit": 1,
    }


# ------------------------------------------------------------ widget keys


async def test_widget_key_create_at_the_cap_is_refused() -> None:
    with (
        scoped_app() as (app, world),
        _limits(max_widget_keys=2),
        patch.object(caps, "widget_key_count", AsyncMock(return_value=2)),
    ):
        res = await call(
            app,
            "owner_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/widget-keys",
            json={"name": "Third", "allowed_origins": ["https://a.example"]},
        )
    assert res.status_code == 409
    assert res.json() == {
        "detail": "Widget key limit reached (2)",
        "code": "widget_key_limit_reached",
        "limit": 2,
        "used": 2,
    }
    world.session.add.assert_not_called()


async def test_widget_key_rotation_at_the_cap_still_works() -> None:
    from callobuzz_rag.db.models.widget_key import WidgetKey

    with (
        scoped_app() as (app, world),
        _limits(max_widget_keys=1),
        patch.object(caps, "widget_key_count", AsyncMock(return_value=1)),
    ):
        key = SimpleNamespace(
            id=uuid.uuid4(),
            corpus_id=world.corpus_a.id,
            name="Site",
            public_key="pk_" + "a" * 32,
            allowed_origins=["https://a.example"],
            rate_limit_per_minute=10,
            daily_message_limit=1000,
            appearance={},
            is_active=True,
            created_at=datetime.now(UTC),
        )
        world.add_row(WidgetKey, key)
        res = await call(
            app,
            "owner_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/widget-keys/{key.id}/rotate",
        )
    assert res.status_code == 200, res.text
    assert res.json()["public_key"] != "pk_" + "a" * 32


async def test_widget_key_count_includes_disabled_keys() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = 4
    session.execute = AsyncMock(return_value=result)
    assert await caps.widget_key_count(session, uuid.uuid4()) == 4
    sql = str(session.execute.await_args.args[0])
    assert "JOIN corpora" in sql and "is_active" not in sql
