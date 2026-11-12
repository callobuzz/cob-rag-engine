"""`answer_gate` (PIP3 task 4, the gate itself): what it reads, what it counts, what it hands the
route. The dependencies are patched at the gate's own names, so each test says exactly which
fact it assumes (usage, billing, the downgrade target); the routes are in
`test_fairuse_gate_routes.py`."""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.context import WAIVED_REASON
from callobuzz_rag.billing.enforcement import NOT_BILLED, BillingState
from callobuzz_rag.fairuse import gate
from callobuzz_rag.fairuse.limits import (
    Breach,
    EffectiveFairUse,
    FairUseLimits,
    FairUsePolicy,
)
from callobuzz_rag.fairuse.usage import FairUseUsage
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig

PAUSED = BillingState(True, True, "prepaid_empty", -120, "INR")


def prof(name: str, *, route: str = "engine", available: bool = True, **config: Any):
    return EffectiveProfile(
        id=uuid.uuid4(),
        name=name,
        label=name,
        config=ProfileConfig(
            answer_route=route,  # type: ignore[arg-type]
            overrides_kb_settings=route == "local",
            **config,
        ),
        source="plan",
        available=available,
    )


STD = prof("Standard")
FREE = prof("Free", route="local", priority="low")


def effective(
    limits: dict[str, Any] | None = None, *, policy: dict[str, Any] | None = None, **kw: Any
) -> EffectiveFairUse:
    return EffectiveFairUse(
        limits=FairUseLimits(**(limits or {})),
        policy=FairUsePolicy(**(policy or {})),
        exempt=kw.pop("exempt", False),
        overridden=frozenset(),
        tenant_name="Shree Dental Clinic, Pune",
        **kw,
    )


def request(*, admin: bool = False, enabled: bool = True) -> Any:
    return SimpleNamespace(
        state=SimpleNamespace(is_admin=admin),
        app=SimpleNamespace(
            state=SimpleNamespace(settings=SimpleNamespace(fair_use_enabled=enabled))
        ),
    )


@contextmanager
def env(
    eff: EffectiveFairUse,
    *,
    usage: FairUseUsage | None = None,
    billing: BillingState = NOT_BILLED,
    engine: Breach | None = None,
    target: EffectiveProfile | None = FREE,
    cap: Exception | None = None,
) -> Any:
    spies = SimpleNamespace(
        cap=AsyncMock(side_effect=cap),
        billing=AsyncMock(return_value=billing),
        usage=AsyncMock(return_value=usage or FairUseUsage()),
        engine=AsyncMock(return_value=engine),
        hit=AsyncMock(return_value=True),
        schedule=MagicMock(),
        target=AsyncMock(return_value=target),
    )
    with (
        patch.object(gate, "effective_fair_use", AsyncMock(return_value=eff)),
        patch.object(gate, "enforce_question_cap", spies.cap),
        patch.object(gate, "enforce_billing", spies.billing),
        patch.object(gate, "read_usage", spies.usage),
        patch.object(gate.rails, "engine_breach", spies.engine),
        patch.object(gate.counters, "hit_question", spies.hit),
        patch.object(gate.alerts, "schedule", spies.schedule),
        patch.object(gate.tier_profiles, "load_profile", spies.target),
        patch.object(gate, "get_redis", MagicMock()),
    ):
        yield spies


async def run(
    req: Any | None = None,
    *,
    payer: str = "operator",
    surface: str = "api",
    count: bool = True,
    enforce_cap: bool = True,
    plan: EffectiveProfile = STD,
) -> gate.GateDecision:
    corpus = SimpleNamespace(tenant_id=uuid.uuid4())
    return await gate.answer_gate(
        AsyncMock(),
        req or request(),
        corpus,  # type: ignore[arg-type]
        payer=payer,
        surface=surface,  # type: ignore[arg-type]
        count_question=count,
        enforce_cap=enforce_cap,
        plan_profile=plan,
    )


async def test_a_business_without_limits_costs_nothing_extra() -> None:
    with env(effective()) as s:
        d = await run()
    assert (d.action, d.profile) == ("allow", STD)
    s.usage.assert_not_awaited()
    s.hit.assert_not_awaited()
    s.schedule.assert_not_called()  # an allowed answer below every alert level


async def test_the_monthly_cap_is_asked_first_and_its_error_is_untouched() -> None:
    boom = APIError(429, "Monthly question limit reached", "question_cap_reached")
    with env(effective({"questions_per_hour": 30}), cap=boom) as s, pytest.raises(APIError) as err:
        await run()
    assert err.value.code == "question_cap_reached"
    s.usage.assert_not_awaited()
    s.cap.assert_awaited_once()
    assert (
        s.cap.await_args.kwargs["widget"] is False
        and s.cap.await_args.kwargs["payer"] == "operator"
    )


async def test_the_widget_gets_the_widget_wording_of_the_cap() -> None:
    with env(effective()) as s:
        await run(surface="widget")
    assert s.cap.await_args.kwargs["widget"] is True


async def test_a_cached_answer_of_a_plan_that_does_not_count_them_skips_the_cap() -> None:
    with env(effective()) as s:
        await run(enforce_cap=False)
    s.cap.assert_not_awaited()


async def test_soft_breach_answers_slower_on_the_fallback_and_is_counted() -> None:
    eff = effective({"questions_per_hour": 30})
    with env(eff, usage=FairUseUsage(questions_per_hour=31)) as s:
        d = await run()
    assert d.action == "downgrade" and d.profile.name == "Free"
    assert d.profile.source == "downgrade" and d.profile.config.priority == "low"
    assert d.waive_charges is False  # a fair-use downgrade is still charged
    s.hit.assert_awaited_once()  # an answered question counts in the windows
    s.schedule.assert_called_once()
    assert WAIVED_REASON.get() is None


async def test_hard_breach_pauses_and_is_not_counted() -> None:
    eff = effective({"questions_per_hour": 30})
    with env(eff, usage=FairUseUsage(questions_per_hour=46)) as s:
        d = await run()
    assert (d.action, d.paused_by, d.reasons[0]) == ("pause", "fair_use", "fair_use_hard")
    assert d.retry_after_s and d.retry_after_s > 0
    s.hit.assert_not_awaited()  # a paused visitor must not push the count further
    s.schedule.assert_called_once()


async def test_only_per_day_limits_never_touch_the_windows() -> None:
    eff = effective({"questions_per_day": 300})
    with env(eff, usage=FairUseUsage(questions_per_day=5)) as s:
        await run()
    s.hit.assert_not_awaited()
    assert s.usage.await_args.kwargs["metrics"] == ["questions_per_day"]


async def test_only_the_limited_metrics_are_read() -> None:
    eff = effective({"questions_per_hour": 30, "fetches_per_day": 50, "storage_mb": 100})
    with env(eff) as s:
        await run()
    assert s.usage.await_args.kwargs["metrics"] == [
        "questions_per_hour"
    ]  # not fetches, not storage


async def test_a_balance_that_ran_out_downgrades_for_free() -> None:
    with env(effective(), billing=PAUSED) as s:
        d = await run()
    assert (d.action, d.reasons[0], d.waive_charges) == ("downgrade", "billing_block", True)
    assert WAIVED_REASON.get() == "downgraded"  # the charge row is written waived, at zero
    s.hit.assert_not_awaited()  # no minute/hour limit, nothing to count
    s.schedule.assert_called_once()


async def test_the_waiver_never_carries_over_to_the_next_request() -> None:
    with env(effective(), billing=PAUSED):
        await run()
    assert WAIVED_REASON.get() == "downgraded"
    with env(effective()):
        await run()
    assert WAIVED_REASON.get() is None


async def test_no_balance_and_no_local_model_pauses_for_billing() -> None:
    down = prof("Free", route="local", available=False)
    with env(effective(), billing=PAUSED, target=down):
        d = await run()
    assert (d.action, d.paused_by) == ("pause", "billing")


async def test_a_downgrade_target_that_cannot_be_loaded_pauses() -> None:
    with env(effective(), billing=PAUSED, target=None):
        d = await run()
    assert (d.action, d.paused_by) == ("pause", "billing")


async def test_the_policys_own_downgrade_profile_is_the_one_loaded() -> None:
    chosen = uuid.uuid4()
    with env(effective(policy={"downgrade_profile_id": chosen}), billing=PAUSED) as s:
        await run()
    assert s.target.await_args.args[1] == chosen


async def test_the_admin_principal_skips_everything_and_is_not_counted() -> None:
    eff = effective({"questions_per_hour": 1})
    with env(eff, billing=PAUSED, usage=FairUseUsage(questions_per_hour=999)) as s:
        d = await run(request(admin=True))
    assert (d.action, d.billing) == ("allow", NOT_BILLED)
    s.usage.assert_not_awaited()
    s.hit.assert_not_awaited()
    s.schedule.assert_not_called()


async def test_exempt_workspaces_are_never_limited() -> None:
    eff = effective({"questions_per_hour": 1}, exempt=True)
    with env(eff, usage=FairUseUsage(questions_per_hour=999)) as s:
        d = await run()
    assert d.action == "allow"
    s.usage.assert_not_awaited()


async def test_a_byok_answer_is_not_limited_by_the_deployers_spend() -> None:
    eff = effective({"ai_spend_per_day_usd_micro": 1_000, "questions_per_hour": 30})
    breach = Breach("engine_ai_spend_per_day", "hard", 10, 10, None)
    with env(eff, engine=breach) as s:
        d = await run(payer="byok")
    assert d.action == "allow"
    assert s.usage.await_args.kwargs["metrics"] == ["questions_per_hour"]  # no spend metric
    s.engine.assert_not_awaited()


async def test_the_engine_ceiling_sends_operator_paid_answers_to_the_local_model() -> None:
    breach = Breach("engine_ai_spend_per_day", "hard", 5_000_000, 5_000_000, None)
    with env(effective(), engine=breach):
        d = await run()
    assert (d.action, d.reasons[0], d.profile.name) == ("downgrade", "engine_spend_ceiling", "Free")


async def test_a_spend_ceiling_without_a_local_model_pauses() -> None:
    eff = effective({"ai_spend_per_day_usd_micro": 5_000_000})
    down = prof("Free", route="local", available=False)
    with env(eff, usage=FairUseUsage(spend_day_usd_micro=5_000_000), target=down):
        d = await run()
    assert (d.action, d.reasons[0], d.paused_by) == ("pause", "spend_ceiling", "fair_use")


async def test_the_spend_read_failing_is_treated_as_the_ceiling() -> None:
    eff = effective({"ai_spend_per_day_usd_micro": 5_000_000})
    with env(eff, usage=FairUseUsage(spend_unknown=True)):
        d = await run()
    assert (d.action, d.reasons[0]) == ("downgrade", "spend_unknown")


async def test_the_concurrency_limit_becomes_the_profiles_per_business_slots() -> None:
    eff = effective({"concurrent_answers": 2})
    with env(eff):
        d = await run()
    assert d.profile.config.max_concurrency == 2
    wide = prof("Wide", max_concurrency=8)
    corpus = SimpleNamespace(tenant_id=uuid.uuid4())
    with env(eff):
        d2 = await gate.answer_gate(
            AsyncMock(),
            request(),
            corpus,
            payer="operator",
            surface="api",
            plan_profile=wide,  # type: ignore[arg-type]
        )
    assert d2.profile.config.max_concurrency == 2  # the smaller of the two wins


async def test_switched_off_only_the_cap_and_billing_apply() -> None:
    eff = effective({"questions_per_hour": 1})
    with env(eff, usage=FairUseUsage(questions_per_hour=999)) as s:
        d = await run(request(enabled=False))
    assert d.action == "allow"
    s.usage.assert_not_awaited()
    s.hit.assert_not_awaited()
    s.schedule.assert_not_called()
    with env(eff, billing=PAUSED):
        paused = await run(request(enabled=False))
    assert paused.action == "downgrade"  # the plan's billing rule still applies


async def test_a_stored_state_that_may_need_to_clear_schedules_the_resume() -> None:
    with env(effective(state="stopped")) as s:
        d = await run()
    assert d.action == "allow"
    s.schedule.assert_called_once()  # no breach now: the task moves the state back to ok


# ----------------------------------------------------------------- what a pause means


def test_pause_args_keep_the_billing_flag_for_a_balance_pause() -> None:
    billing = gate.GateDecision(
        "pause", STD, ("billing_block",), (), PAUSED, "billing", False, None
    )
    fair = gate.GateDecision(
        "pause", STD, ("fair_use_hard",), (), NOT_BILLED, "fair_use", False, 60
    )
    allow = gate.GateDecision("allow", STD, ("ok",), (), NOT_BILLED, None, False, None)
    assert gate.pause_args(billing) == {"billing_paused": True, "paused_by": "billing"}
    assert gate.pause_args(fair) == {"billing_paused": False, "paused_by": "fair_use"}
    assert gate.pause_args(allow) == {"billing_paused": False, "paused_by": None}


async def test_the_legacy_path_gets_a_429_for_fair_use_and_a_402_for_the_balance() -> None:
    resets = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
    worst = Breach("questions_per_hour", "hard", 30, 46, resets)
    fair = gate.GateDecision(
        "pause", STD, ("fair_use_hard",), (worst,), NOT_BILLED, "fair_use", False, 2400
    )
    with pytest.raises(APIError) as err:
        await gate.refuse_paused(fair, request())  # type: ignore[arg-type]
    e = err.value
    assert (e.status_code, e.code) == (429, "fair_use_limit")
    assert e.extra == {
        "metric": "questions_per_hour",
        "limit": 30,
        "used": 46,
        "resets_at": "2026-10-05T15:00:00Z",
    }
    assert e.headers["Retry-After"] == "2400"

    billing = gate.GateDecision(
        "pause", STD, ("billing_block",), (), PAUSED, "billing", False, None
    )
    with (
        patch("callobuzz_rag.accounts.caps._support_contact", AsyncMock(return_value="help@x.in")),
        pytest.raises(APIError) as err2,
    ):
        await gate.refuse_paused(billing, request())  # type: ignore[arg-type]
    assert (err2.value.status_code, err2.value.code) == (402, "billing_paused")

    ok = gate.GateDecision("downgrade", FREE, ("fair_use_soft",), (), NOT_BILLED, None, False, None)
    await gate.refuse_paused(ok, request())  # type: ignore[arg-type]  # nothing to refuse


def test_a_spend_pause_never_tells_the_caller_the_deployers_cost() -> None:
    worst = Breach("ai_spend_per_day", "hard", 5_000_000, 5_000_000, None)
    d = gate.GateDecision(
        "pause", STD, ("spend_ceiling",), (worst,), NOT_BILLED, "fair_use", False, 60
    )
    body = gate.fair_use_error(d)
    assert body.extra["metric"] == "ai_spend_per_day"
    assert "limit" not in body.extra and "used" not in body.extra


# ----------------------------------------------------------------------------------- COB


async def test_cob_is_blocked_only_past_its_own_daily_questions() -> None:
    eff = effective({"cob_questions_per_day": 20})
    with (
        patch.object(gate, "effective_fair_use", AsyncMock(return_value=eff)),
        patch.object(
            gate, "read_usage", AsyncMock(return_value=FairUseUsage(cob_questions_per_day=19))
        ),
    ):
        assert await gate.cob_blocked(AsyncMock(), uuid.uuid4()) is False
    with (
        patch.object(gate, "effective_fair_use", AsyncMock(return_value=eff)),
        patch.object(
            gate, "read_usage", AsyncMock(return_value=FairUseUsage(cob_questions_per_day=20))
        ),
    ):
        assert await gate.cob_blocked(AsyncMock(), uuid.uuid4()) is True
        assert await gate.cob_blocked(AsyncMock(), uuid.uuid4(), enabled=False) is False


async def test_cob_answers_when_the_read_fails_or_the_workspace_is_exempt() -> None:
    boom = AsyncMock(side_effect=ConnectionError("down"))
    with patch.object(gate, "effective_fair_use", boom):
        assert await gate.cob_blocked(AsyncMock(), uuid.uuid4()) is False
    exempt = effective({"cob_questions_per_day": 1}, exempt=True)
    with patch.object(gate, "effective_fair_use", AsyncMock(return_value=exempt)):
        assert await gate.cob_blocked(AsyncMock(), uuid.uuid4()) is False
    unlimited = effective()
    with patch.object(gate, "effective_fair_use", AsyncMock(return_value=unlimited)):
        assert await gate.cob_blocked(AsyncMock(), uuid.uuid4()) is False


async def test_a_free_plan_with_the_local_model_down_pauses_instead_of_using_the_paid_model() -> None:
    down = prof("Free", route="local", available=False, priority="low")
    with env(effective()) as s:
        d = await run(plan=down)
    assert (d.action, d.paused_by, d.reasons) == (
        "pause",
        "local_unavailable",
        ("local_unavailable",),
    )
    s.hit.assert_not_awaited()


async def test_the_admin_preview_of_a_free_plan_pauses_too_but_is_not_billed() -> None:
    down = prof("Free", route="local", available=False)
    with env(effective()):
        d = await run(request(admin=True), plan=down)
    assert d.action == "pause" and d.billing is NOT_BILLED


async def test_a_business_paying_its_own_ai_is_not_paused_by_the_local_model() -> None:
    down = prof("Free", route="local", available=False)
    with env(effective()):
        d = await run(payer="byok", plan=down)
    assert d.action == "allow"


async def test_a_free_plan_with_the_local_model_ready_answers() -> None:
    with env(effective()):
        d = await run(plan=prof("Free", route="local"))
    assert d.action == "allow"


async def test_a_local_model_that_is_not_ready_is_a_503_not_a_usage_limit_429() -> None:
    """FU-9: legacy callers must not chase a usage limit that was never reached."""
    down = prof("Free", route="local", available=False, priority="low")
    with env(effective()):
        d = await run(plan=down)
    with pytest.raises(Exception) as err:
        await gate.refuse_paused(d, request())
    assert getattr(err.value, "status_code", None) == 503
    assert getattr(err.value, "code", None) == "local_model_unavailable"
    assert int(err.value.headers["Retry-After"]) >= 1
    args = gate.pause_args(d)
    assert args["paused_by"] == "local_unavailable" and args["billing_paused"] is False


async def test_a_local_model_that_is_not_ready_does_not_mark_the_business_stopped() -> None:
    from callobuzz_rag.fairuse import alerts

    down = prof("Free", route="local", available=False, priority="low")
    with env(effective()):
        d = await run(plan=down)
    assert alerts.state_of(d, [], "ok") == "ok"
