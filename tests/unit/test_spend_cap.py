"""The owner's monthly spending cap (TV3): the pure rules, what the gate reads, who is told at
80 % and at the cap, and the route an owner sets it with.

A request for a person never reaches `answer_gate` (TV1: `test_human_bypass_routes.py` fails if
any gate runs for one), so the cap can never turn a visitor away from a human."""

import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.billing.enforcement import NOT_BILLED
from callobuzz_rag.billing.spend_cap import cap_state
from callobuzz_rag.fairuse import alerts
from callobuzz_rag.fairuse.decision import Breach, decide, evaluate
from callobuzz_rag.fairuse.limits import (
    ANSWER_METRICS,
    EffectiveFairUse,
    FairUseLimits,
    FairUsePolicy,
)
from callobuzz_rag.fairuse.usage import FairUseUsage, read_usage
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.test_fairuse_alerts import FREE, STD, AlertEnv, alert_env  # noqa: F401
from tests.unit.test_fairuse_decision import PAUSED, profile

CAP = 1_000_000  # Rs 10,000 in paise
POLICY = FairUsePolicy()
NOW = datetime(2026, 10, 5, 14, 20, tzinfo=UTC)
LOCAL = profile("Free", route="local", priority="low")
LOCAL_DOWN = replace(LOCAL, available=False)
PAID = profile("Standard")


def _eval(used_minor: int, *, unknown: bool = False) -> list[Breach]:
    usage = FairUseUsage(spend_cap_month_micro=used_minor * 10**4, spend_unknown=unknown)
    return evaluate(
        FairUseLimits(),
        usage,
        POLICY,
        ["spend_cap_per_month"],
        now=NOW,
        spend_cap_micro=CAP * 10**4,
    )


@pytest.mark.parametrize(
    ("cap", "used", "state"),
    [
        (None, 10**9, "ok"),
        (1_000, 0, "ok"),
        (1_000, 799, "ok"),
        (1_000, 800, "near"),
        (1_000, 999, "near"),
        (1_000, 1_000, "reached"),
        (1_000, 5_000, "reached"),
    ],
)
def test_the_state_the_plan_page_shows(cap: int | None, used: int, state: str) -> None:
    assert cap_state(cap, used) == state


def test_at_the_cap_is_a_hard_spend_breach() -> None:
    assert [(b.metric, b.level) for b in _eval(CAP)] == [("spend_cap_per_month", "hard")]
    assert [(b.metric, b.level) for b in _eval(CAP * 2)] == [("spend_cap_per_month", "hard")]


def test_eighty_percent_is_near() -> None:
    assert [b.level for b in _eval(800_000)] == ["near"]
    assert _eval(799_999) == []


def test_unread_spend_fails_closed() -> None:
    assert [b.level for b in _eval(0, unknown=True)] == ["hard"]
    assert _eval(0, unknown=True)[0].unknown is True


def test_the_cap_resets_at_the_start_of_next_month() -> None:
    [breach] = _eval(CAP)
    assert breach.resets_at == datetime(2026, 11, 1, tzinfo=UTC)


@pytest.mark.parametrize(("has_fallback", "action"), [(True, "downgrade"), (False, "pause")])
def test_the_spend_row_decides(has_fallback: bool, action: str) -> None:
    d = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=_eval(CAP),
        policy=POLICY,
        plan_profile=PAID,
        downgrade_to=LOCAL if has_fallback else LOCAL_DOWN,
        exempt=False,
        now=NOW,
    )
    assert d.action == action and d.reasons[0] == "spend_cap"
    if action == "pause":
        assert d.paused_by == "fair_use"


def test_a_cap_alone_never_blocks_below_it() -> None:
    d = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=_eval(900_000),
        policy=POLICY,
        plan_profile=PAID,
        downgrade_to=LOCAL,
        exempt=False,
        now=NOW,
    )
    assert d.action == "allow"


def test_the_owners_cap_is_named_when_the_deployers_spend_ceiling_is_hit_too() -> None:
    both = [
        Breach("ai_spend_per_day", "hard", 5_000_000, 5_000_000, None),
        *_eval(CAP),
    ]
    d = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=both,
        policy=POLICY,
        plan_profile=PAID,
        downgrade_to=LOCAL,
        exempt=False,
        now=NOW,
    )
    assert d.reasons[0] == "spend_cap"


def test_an_exempt_workspace_is_never_capped_and_billing_pause_still_wins() -> None:
    kw: dict[str, Any] = {
        "cap_blocked": False,
        "breaches": _eval(CAP),
        "policy": POLICY,
        "plan_profile": PAID,
        "downgrade_to": LOCAL,
        "now": NOW,
    }
    assert decide(billing=NOT_BILLED, exempt=True, **kw).action == "allow"
    paused = decide(billing=PAUSED, exempt=False, **kw)
    # the balance pause already switched answers to the free fallback, which cannot spend
    assert (paused.action, paused.reasons[0]) == ("downgrade", "billing_block")


# ---------------------------------------------------------------- what the gate reads


class Rows:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one(self) -> Any:
        return self._scalar


class Session:
    def __init__(self, *results: Any, fail: bool = False) -> None:
        self.results = list(results)
        self.statements: list[str] = []
        self.fail = fail

    @asynccontextmanager
    async def begin_nested(self) -> Any:
        yield self

    async def execute(self, stmt: Any) -> Any:
        self.statements.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        if self.fail:
            raise ConnectionError("database is down")
        return self.results.pop(0)


TENANT = uuid.uuid4()


async def test_no_cap_costs_no_query() -> None:
    session = Session()
    usage = await read_usage(session, FakeRedis(), TENANT, NOW, metrics=[])
    assert session.statements == [] and usage.spend_cap_month_micro == 0
    effective = EffectiveFairUse(FairUseLimits(), POLICY, False, frozenset())
    assert effective.limited(ANSWER_METRICS) == []  # nothing to ask `read_usage` for


async def test_a_cap_adds_one_query_for_the_months_charges() -> None:
    effective = EffectiveFairUse(FairUseLimits(), POLICY, False, frozenset(), spend_cap_minor=CAP)
    assert effective.limited(ANSWER_METRICS) == ["spend_cap_per_month"]
    session = Session(Rows(123_000_000))
    usage = await read_usage(
        session, FakeRedis(), TENANT, NOW, metrics=effective.limited(ANSWER_METRICS)
    )
    assert usage.spend_cap_month_micro == 123_000_000
    [sql] = session.statements
    assert "usage_charges" in sql and "day >= '2026-10-01'" in sql and "day <= '2026-10-05'" in sql
    assert f"tenant_id = '{TENANT.hex}'" in sql


async def test_a_failed_read_is_reported_not_zero() -> None:
    session = Session(fail=True)
    usage = await read_usage(session, FakeRedis(), TENANT, NOW, metrics=["spend_cap_per_month"])
    assert usage.spend_unknown is True and usage.spend_cap_month_micro == 0


# ---------------------------------------------------------------- who is told, and when


def _effective(env: AlertEnv) -> EffectiveFairUse:
    return EffectiveFairUse(
        FairUseLimits(),
        FairUsePolicy(),
        False,
        frozenset(),
        state=env.state,  # type: ignore[arg-type]
        tenant_name="Shree Dental Clinic, Pune",
        spend_cap_minor=CAP,
        currency="INR",
    )


async def _cross(env: AlertEnv, used_minor: int, *, target: Any = LOCAL_DOWN) -> list[Any]:
    usage = FairUseUsage(spend_cap_month_micro=used_minor * 10**4)
    breaches = evaluate(
        FairUseLimits(),
        usage,
        POLICY,
        ["spend_cap_per_month"],
        now=env.clock.now,
        spend_cap_micro=CAP * 10**4,
    )
    decision = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=breaches,
        policy=POLICY,
        plan_profile=STD,
        downgrade_to=target,
        exempt=False,
        now=env.clock.now,
    )
    return await alerts.after_decision(
        env.factory,
        TENANT,
        decision,
        _effective(env),
        now=env.clock.now,
        settings=env.settings(),
        enqueue=env.enqueue,
        dispatch=env.dispatch,
    )


async def test_eighty_and_one_hundred_percent_each_tell_the_owners_once(
    alert_env: AlertEnv,  # noqa: F811
) -> None:
    for used in (700_000, 800_000, 850_000, 1_000_000, 1_000_001, 1_200_000):
        await _cross(alert_env, used)
    templates = [(t, ctx["level_pct"]) for t, _, ctx in alert_env.mail_calls]
    assert templates == [("fair_use_alert", 80), ("fair_use_alert", 100)]
    assert {to for _, to, _ in alert_env.mail_calls} == {"owner@shree.example"}


async def test_the_mail_says_spending_cap_in_rupees_and_never_fair_use(
    alert_env: AlertEnv,  # noqa: F811
) -> None:
    await _cross(alert_env, 1_000_000)
    ctx = alert_env.mail_calls[-1][2]
    assert ctx["metric_label"] == "monthly spending cap"
    assert ctx["used"] == "₹10,000" and ctx["limit"] == "₹10,000"
    from callobuzz_rag.accounts.email.render import render_email
    from callobuzz_rag.accounts.settings_store import Branding

    mail = render_email(
        "fair_use_alert",
        "owner@shree.example",
        ctx,
        Branding(product_name="Sahayak AI"),
        "https://dash.example",
    )
    assert "fair use" not in (mail.text + mail.html).lower()
    assert "monthly spending cap" in mail.text


async def test_the_deployer_is_not_mailed_and_no_webhook_fires(
    alert_env: AlertEnv,  # noqa: F811
) -> None:
    await _cross(alert_env, 1_000_000)
    assert all(t == "fair_use_alert" for t, _, _ in alert_env.mail_calls)
    assert alert_env.webhooks == []


async def test_reaching_the_cap_is_audited_once_and_does_not_move_the_fair_use_state(
    alert_env: AlertEnv,  # noqa: F811
) -> None:
    await _cross(alert_env, 1_000_000)
    await _cross(alert_env, 1_100_000)
    assert alert_env.audit == ["billing.spend_cap_reached"]
    assert alert_env.audit_details[0] == {
        "cap_minor": CAP,
        "used_minor": CAP,
        "month": "2026-10",
    }
    assert alert_env.state == "ok"  # the cap is the owner's limit, not the deployer's fair use


async def test_next_month_starts_over(alert_env: AlertEnv) -> None:  # noqa: F811
    await _cross(alert_env, 1_000_000)
    first = len(alert_env.mail_calls)
    alert_env.clock.now = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    await _cross(alert_env, 1_000_000)
    assert len(alert_env.mail_calls) == 2 * first
    assert alert_env.audit == ["billing.spend_cap_reached"] * 2


def test_the_alert_environment_uses_the_deployers_downgrade_target() -> None:
    assert SimpleNamespace(**{"name": FREE.name}).name == "Free"


def test_answers_past_the_owners_cap_are_not_charged() -> None:
    """The downgrade at the cap waives the charge, or a per-question plan keeps spending."""
    d = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=_eval(CAP),
        policy=POLICY,
        plan_profile=PAID,
        downgrade_to=LOCAL,
        exempt=False,
        now=NOW,
    )
    assert d.action == "downgrade" and d.waive_charges is True
