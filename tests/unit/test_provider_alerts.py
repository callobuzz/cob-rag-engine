"""BIL1: provider low-balance alerts, once per crossing."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.accounts.email.render import validate_context
from callobuzz_rag.analytics.provider_balances import ProviderBalance
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.events import WebhookEvent
from callobuzz_rag.db.models.provider_balance_setting import ProviderBalanceSetting
from callobuzz_rag.worker import provider_tasks
from callobuzz_rag.worker.provider_tasks import run_checks
from tests.unit.test_auth_routes import World

NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


@pytest.fixture
def world() -> World:
    return World()


def balance(row: ProviderBalanceSetting, status: str, remaining: int | None = 3_200_000) -> Any:
    return ProviderBalance(
        provider="manual",
        label=row.label,
        status=status,  # type: ignore[arg-type]
        reading=None,
        manual_remaining_usd_micro=None,
        manual_since=None,
        recorded_month_usd_micro=0,
        low_threshold_usd_micro=5_000_000,
        checked_at=NOW,
        error=None,
        id=str(row.id),
        remaining_usd_micro=remaining,
    )


async def run(world: World, states: list[str], *, email: str = "sv@example.com") -> Any:
    row = world.db.add(ProviderBalanceSetting(provider="manual", label="Groq", alert_state="ok"))
    mails: list[tuple[str, str, dict[str, Any]]] = []
    hooks: list[tuple[WebhookEvent, dict[str, Any]]] = []

    def enqueue(template: str, to: str, context: Any, *, expires_s: float) -> None:
        validate_context(template, to, context)  # the real template contract
        mails.append((template, to, dict(context)))

    async def dispatch(event: WebhookEvent, data: dict[str, Any]) -> None:
        hooks.append((event, data))

    settings = Settings(_env_file=None, superadmin_email=email)  # type: ignore[call-arg]
    results = []
    for status in states:

        async def balances(*a: Any, status: str = status, **kw: Any) -> list[Any]:
            assert kw.get("refresh") is True  # the check always reads past the cache
            return [balance(row, status)]

        results.append(
            await run_checks(
                world.db.factory(),
                settings,
                dispatch=dispatch,
                enqueue=enqueue,
                now=NOW,
                balances=balances,
            )
        )
    return SimpleNamespace(mails=mails, hooks=hooks, row=row, results=results)


async def test_alerts_once_per_crossing_and_rearms(world: World) -> None:
    out = await run(world, ["ok", "low", "low", "empty", "empty", "ok", "low"])
    assert [m[2]["status"] for m in out.mails] == ["low", "empty", "low"]
    assert [h[0] for h in out.hooks] == [WebhookEvent.BILLING_PROVIDER_LOW_BALANCE] * 3
    assert out.hooks[0][1] == {
        "provider": "manual",
        "status": "low",
        "remaining_usd": "3.200000",
        "threshold_usd": "5.000000",
    }
    assert out.mails[0][1] == "sv@example.com"
    assert out.mails[0][2]["remaining"] == "$3.20" and out.mails[0][2]["threshold"] == "$5.00"
    assert out.row.alert_state == "low" and out.row.alerted_at == NOW


async def test_unknown_never_alerts_and_never_rearms(world: World) -> None:
    out = await run(world, ["low", "unknown", "low"])
    assert len(out.mails) == 1 and len(out.hooks) == 1
    assert out.row.alert_state == "low"


async def test_improving_from_empty_to_low_is_silent(world: World) -> None:
    out = await run(world, ["empty", "low"])
    assert [m[2]["status"] for m in out.mails] == ["empty"]
    assert out.row.alert_state == "low"


async def test_no_superadmin_email_skips_mail_but_sends_webhook(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    out = await run(world, ["low"], email="")
    assert out.mails == [] and len(out.hooks) == 1
    assert out.results[0].skipped_no_recipient == 1
    assert "RAG_SUPERADMIN_EMAIL" in caplog.text


async def test_state_is_committed_before_anything_is_sent(world: World) -> None:
    row = world.db.add(ProviderBalanceSetting(provider="manual", label="Groq", alert_state="ok"))
    seen: list[tuple[int, str]] = []

    async def dispatch(event: WebhookEvent, data: dict[str, Any]) -> None:
        seen.append((world.db.commits, row.alert_state))

    async def balances(*a: Any, **kw: Any) -> list[Any]:
        return [balance(row, "empty", 0)]

    await run_checks(
        world.db.factory(),
        Settings(_env_file=None, superadmin_email=""),  # type: ignore[call-arg]
        dispatch=dispatch,
        enqueue=lambda *a, **k: None,
        balances=balances,
    )
    assert seen == [(1, "empty")]


async def test_a_failing_webhook_or_mail_never_breaks_the_check(world: World) -> None:
    row = world.db.add(ProviderBalanceSetting(provider="manual", label="Groq", alert_state="ok"))

    async def dispatch(event: WebhookEvent, data: dict[str, Any]) -> None:
        raise RuntimeError("down")

    def enqueue(*a: Any, **k: Any) -> None:
        raise RuntimeError("broker")

    async def balances(*a: Any, **kw: Any) -> list[Any]:
        return [balance(row, "low")]

    res = await run_checks(
        world.db.factory(),
        Settings(_env_file=None, superadmin_email="sv@example.com"),  # type: ignore[call-arg]
        dispatch=dispatch,
        enqueue=enqueue,
        balances=balances,
    )
    assert res.alerts == 1 and res.mails == 0 and res.webhooks == 0


def test_task_and_schedule_are_registered() -> None:
    from callobuzz_rag.worker.beat_schedule import build_beat_schedule

    entry = build_beat_schedule(Settings(_env_file=None))["check-provider-balances"]  # type: ignore[call-arg]
    assert entry["task"] == "callobuzz_rag.check_provider_balances"
    assert entry["schedule"] == 900.0 and entry["options"] == {"queue": "periodic"}
    assert provider_tasks.check_provider_balances_task.name == entry["task"]


def test_money_formatting() -> None:
    assert provider_tasks._money(3_200_000) == "$3.20"
    assert provider_tasks._money(-1_500_000) == "-$1.50"
    assert provider_tasks._money(None) == "unknown"
    assert provider_tasks._usd(1_234_567) == "1.234567"


async def test_ensure_row_is_conflict_safe_and_the_alert_row_is_created_once(world: World) -> None:
    from callobuzz_rag.analytics.provider_balances import ensure_row

    session = world.db.session()
    first, created_first = await ensure_row(session, "openai", "OpenAI")  # type: ignore[arg-type]
    second, created_second = await ensure_row(session, "openai", "OpenAI", lock=True)  # type: ignore[arg-type]
    assert (created_first, created_second) == (True, False) and first is second
    assert len(world.db.all(ProviderBalanceSetting)) == 1


async def test_first_alert_of_an_api_provider_without_a_row_is_remembered(world: World) -> None:
    def api_balance(status: str) -> Any:
        return ProviderBalance(
            provider="openai",
            label="OpenAI",
            status=status,  # type: ignore[arg-type]
            reading=None,
            manual_remaining_usd_micro=None,
            manual_since=None,
            recorded_month_usd_micro=0,
            low_threshold_usd_micro=5_000_000,
            checked_at=NOW,
            error=None,
            id=None,
            remaining_usd_micro=1_000_000,
        )

    mails: list[Any] = []

    def enqueue(template: str, to: str, context: Any, *, expires_s: float) -> None:
        mails.append(context)

    async def dispatch(event: WebhookEvent, data: dict[str, Any]) -> None:
        return None

    async def balances(*a: Any, **kw: Any) -> list[Any]:
        return [api_balance("low")]

    settings = Settings(_env_file=None, superadmin_email="sv@example.com")  # type: ignore[call-arg]
    for _ in range(2):  # the second run must not create a second row nor alert again
        await run_checks(
            world.db.factory(),
            settings,
            dispatch=dispatch,
            enqueue=enqueue,
            now=NOW,
            balances=balances,
        )
    (row,) = world.db.all(ProviderBalanceSetting)
    assert row.provider == "openai" and row.alert_state == "low"
    assert len(mails) == 1
