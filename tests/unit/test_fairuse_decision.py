"""The one decision for every answer (PIP3 task 3): every row of the decision table, the
properties that must hold for every combination, and how limits turn into breaches.

Pure: no database, no clock, no network."""

import itertools
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from callobuzz_rag.billing.enforcement import NOT_BILLED, BillingState
from callobuzz_rag.fairuse.decision import decide, evaluate, resets_at
from callobuzz_rag.fairuse.limits import Breach, FairUseLimits, FairUsePolicy
from callobuzz_rag.fairuse.usage import FairUseUsage
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig

NOW = datetime(2026, 10, 5, 14, 20, tzinfo=UTC)


def profile(name: str, *, priority: str = "normal", route: str = "engine", available: bool = True):
    config = ProfileConfig(
        answer_route=route,  # type: ignore[arg-type]
        priority=priority,  # type: ignore[arg-type]
        overrides_kb_settings=route == "local",
    )
    return EffectiveProfile(
        id=uuid.uuid4(),
        name=name,
        label=name,
        config=config,
        source="plan",
        available=available,
    )


STD = profile("Standard")
FREE = profile("Free", route="local", priority="low")
FREE_DOWN = replace(FREE, available=False)
OK = NOT_BILLED
PAUSED = BillingState(True, True, "prepaid_empty", -120, "INR")
POLICY = FairUsePolicy()


def pol(**changes: object) -> FairUsePolicy:
    return FairUsePolicy(**changes)  # type: ignore[arg-type]


def breach(metric: str, level: str, *, limit: int = 30, used: int = 31) -> Breach:
    return Breach(metric, level, limit, used, None)  # type: ignore[arg-type]


def soft(metric: str) -> Breach:
    return breach(metric, "soft")


def hard(metric: str) -> Breach:
    return breach(metric, "hard", used=46)


def near(metric: str) -> Breach:
    return breach(metric, "near", used=25)


def unknown_spend() -> Breach:
    return Breach("ai_spend_per_day", "hard", 5_000_000, 0, None, unknown=True)


def run(
    *,
    cap: bool = False,
    billing: BillingState = OK,
    breaches: list[Breach] | None = None,
    policy: FairUsePolicy = POLICY,
    target: EffectiveProfile = FREE,
    exempt: bool = False,
):
    return decide(
        cap_blocked=cap,
        billing=billing,
        breaches=breaches or [],
        policy=policy,
        plan_profile=STD,
        downgrade_to=target,
        exempt=exempt,
        now=NOW,
    )


@pytest.mark.parametrize(
    ("cap", "billing", "breaches", "policy", "target", "want"),
    [
        (True, OK, [], POLICY, FREE, ("refuse", "question_cap")),
        (False, PAUSED, [], POLICY, FREE, ("downgrade", "billing_block")),
        (False, PAUSED, [], POLICY, FREE_DOWN, ("pause", "billing_block")),
        (
            False,
            PAUSED,
            [],
            pol(on_billing_block="pause_handoff"),
            FREE,
            ("pause", "billing_block"),
        ),
        (False, PAUSED, [], pol(on_billing_block="allow"), FREE, ("allow", "ok")),
        (False, OK, [soft("questions_per_hour")], POLICY, FREE, ("downgrade", "fair_use_soft")),
        (False, OK, [hard("questions_per_hour")], POLICY, FREE, ("pause", "fair_use_hard")),
        (
            False,
            OK,
            [soft("questions_per_hour")],
            pol(on_fair_use_exceeded="hard_stop"),
            FREE,
            ("pause", "fair_use_hard"),
        ),
        (
            False,
            OK,
            [hard("questions_per_day")],
            pol(on_fair_use_exceeded="downgrade_only"),
            FREE,
            ("downgrade", "fair_use_soft"),
        ),
        (
            False,
            OK,
            [hard("questions_per_day")],
            pol(on_fair_use_exceeded="alert_only"),
            FREE,
            ("allow", "ok"),
        ),
        (False, OK, [soft("ai_spend_per_day")], POLICY, FREE, ("downgrade", "spend_ceiling")),
        (False, OK, [soft("ai_spend_per_day")], POLICY, FREE_DOWN, ("pause", "spend_ceiling")),
        (False, OK, [unknown_spend()], POLICY, FREE, ("downgrade", "spend_unknown")),
        (False, OK, [near("questions_per_hour")], POLICY, FREE, ("allow", "ok")),
        (False, OK, [soft("questions_per_hour")], POLICY, FREE_DOWN, ("pause", "fair_use_soft")),
        (
            False,
            OK,
            [hard("engine_ai_spend_per_day")],
            pol(on_fair_use_exceeded="alert_only"),
            FREE,
            ("downgrade", "engine_spend_ceiling"),
        ),
    ],
)
def test_decision_table(cap, billing, breaches, policy, target, want) -> None:
    d = run(cap=cap, billing=billing, breaches=breaches, policy=policy, target=target)
    assert (d.action, d.reasons[0]) == want


def test_a_pause_by_billing_and_by_fair_use_say_which() -> None:
    assert run(billing=PAUSED, target=FREE_DOWN).paused_by == "billing"
    assert run(breaches=[hard("questions_per_hour")]).paused_by == "fair_use"
    assert run().paused_by is None
    assert run(billing=PAUSED).paused_by is None  # a downgrade is not a pause


def test_an_unavailable_target_is_named_in_the_reasons() -> None:
    d = run(billing=PAUSED, target=FREE_DOWN)
    assert d.reasons == ("billing_block", "downgrade_unavailable")


def test_billing_downgrade_waives_charges_and_fair_use_downgrade_does_not() -> None:
    assert run(billing=PAUSED).waive_charges is True
    assert run(breaches=[soft("questions_per_hour")]).waive_charges is False
    assert run().waive_charges is False


def test_a_downgrade_answers_on_the_target_at_low_priority() -> None:
    d = run(
        breaches=[soft("questions_per_hour")],
        target=replace(
            FREE,
            config=ProfileConfig(answer_route="local", overrides_kb_settings=True, priority="high"),
        ),
    )
    assert d.profile.name == "Free" and d.profile.source == "downgrade"
    assert d.profile.config.priority == "low"


def test_exempt_always_allows() -> None:
    d = run(
        exempt=True,
        cap=True,
        billing=PAUSED,
        breaches=[hard("questions_per_hour"), unknown_spend()],
    )
    assert (d.action, d.profile) == ("allow", STD)


def test_billing_allow_still_applies_fair_use() -> None:
    d = run(
        billing=PAUSED, policy=pol(on_billing_block="allow"), breaches=[hard("questions_per_day")]
    )
    assert (d.action, d.reasons[0]) == ("pause", "fair_use_hard")


def test_a_business_with_no_balance_cannot_flood_the_fallback_past_a_hard_line() -> None:
    """The one safety beyond the table: row 4 keeps the hard lines."""
    d = run(billing=PAUSED, breaches=[hard("questions_per_hour")])
    assert (d.action, d.paused_by, d.reasons[0]) == ("pause", "fair_use", "fair_use_hard")
    soft_only = run(billing=PAUSED, breaches=[soft("questions_per_hour")])
    assert (soft_only.action, soft_only.waive_charges) == ("downgrade", True)
    alert_only = run(
        billing=PAUSED,
        policy=pol(on_fair_use_exceeded="alert_only"),
        breaches=[hard("questions_per_hour")],
    )
    assert alert_only.action == "downgrade"


CHEAP = profile("Cheap")  # a paid, non-local fallback


@pytest.mark.parametrize("metric", ["ai_spend_per_day", "engine_ai_spend_per_day"])
@pytest.mark.parametrize("exceeded", ["downgrade_only", "alert_only", "throttle_then_stop"])
def test_billing_downgrade_never_answers_on_a_paid_model_past_a_money_ceiling(
    metric, exceeded
) -> None:
    """FU-2: row 4 used to return before the money rows, so a billing-paused business kept
    answering on the deployer's paid key, charges waived, past a ceiling that must stop."""
    d = run(
        billing=PAUSED,
        breaches=[hard(metric)],
        policy=pol(on_fair_use_exceeded=exceeded),
        target=CHEAP,
    )
    assert d.action == "pause" and d.paused_by == "fair_use"
    assert d.reasons[0] in ("spend_ceiling", "engine_spend_ceiling")


def test_billing_downgrade_to_the_free_model_is_the_answer_to_the_engine_rail() -> None:
    """FU-2 reverse: a local target costs the deployer nothing, so the rail moves the business
    to it instead of pausing it as a fair-use breach."""
    d = run(billing=PAUSED, breaches=[hard("engine_ai_spend_per_day")], target=FREE)
    assert (d.action, d.paused_by, d.waive_charges) == ("downgrade", None, True)


def test_a_fair_use_pause_says_when_to_come_back() -> None:
    resets = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
    b = Breach("questions_per_hour", "hard", 30, 46, resets)
    d = run(breaches=[b])
    assert d.retry_after_s == 40 * 60
    assert run(breaches=[near("questions_per_hour")]).retry_after_s is None


BREACH_SETS = [
    list(combo)
    for size in range(3)
    for combo in itertools.combinations(
        [
            breach(m, level)
            for m in ("questions_per_hour", "ai_spend_per_day")
            for level in ("near", "soft", "hard")
        ],
        size,
    )
]
ALL_POLICIES = [
    pol(on_billing_block=b, on_fair_use_exceeded=e)
    for b in ("downgrade_profile", "pause_handoff", "allow")
    for e in ("throttle_then_stop", "hard_stop", "downgrade_only", "alert_only")
]


def test_never_answers_on_the_plan_profile_past_a_hard_line_unless_alert_only() -> None:
    targets = [replace(FREE, available=a) for a in (True, False)] + [CHEAP]
    cases = itertools.product(BREACH_SETS, ALL_POLICIES, (OK, PAUSED), targets)
    checked = 0
    for breaches, policy, billing, target in cases:
        d = run(billing=billing, breaches=breaches, policy=policy, target=target)
        checked += 1
        if d.action == "downgrade" and d.profile.config.answer_route != "local":
            # FU-2: never a paid downgrade while a money ceiling is reached
            assert not any(b.metric == "ai_spend_per_day" and b.level != "near" for b in breaches)
        hard_spend = any(b.level == "hard" and b.metric == "ai_spend_per_day" for b in breaches)
        any_hard = any(b.level == "hard" for b in breaches)
        spend_over = any(b.metric == "ai_spend_per_day" and b.level != "near" for b in breaches)
        if (any_hard and policy.on_fair_use_exceeded != "alert_only") or hard_spend or spend_over:
            assert not (d.action == "allow" and d.profile is STD), (breaches, policy)
        assert d.action != "refuse"  # only the monthly cap refuses
        if d.action == "downgrade":
            assert d.profile.available  # never downgrade to a target that cannot answer
        if d.action == "pause":
            assert d.paused_by in ("billing", "fair_use")
        else:
            assert d.paused_by is None
    assert checked == len(BREACH_SETS) * len(ALL_POLICIES) * 6


def test_money_is_never_allowed_past_its_ceiling_even_with_alert_only() -> None:
    d = run(breaches=[hard("ai_spend_per_day")], policy=pol(on_fair_use_exceeded="alert_only"))
    assert d.action == "downgrade" and d.reasons[0] == "spend_ceiling"


# ------------------------------------------------------------------------ evaluate


def usage(**fields: int) -> FairUseUsage:
    return FairUseUsage(**fields)


def test_levels_near_soft_and_hard() -> None:
    limits = FairUseLimits(questions_per_hour=30)
    metrics = ["questions_per_hour"]
    cases = {23: None, 24: "near", 29: "near", 30: "soft", 44: "soft", 45: "hard", 90: "hard"}
    for used, level in cases.items():
        found = evaluate(limits, usage(questions_per_hour=used), POLICY, metrics, now=NOW)
        assert [b.level for b in found] == ([level] if level else []), used


def test_the_hard_line_is_the_policys() -> None:
    limits = FairUseLimits(questions_per_day=100)
    found = evaluate(
        limits, usage(questions_per_day=100), pol(hard_limit_pct=100), ["questions_per_day"]
    )
    assert [b.level for b in found] == ["hard"]


def test_alert_levels_decide_where_near_starts() -> None:
    limits = FairUseLimits(questions_per_day=100)
    only_100 = pol(alert_thresholds_pct=[100])
    assert evaluate(limits, usage(questions_per_day=95), only_100, ["questions_per_day"]) == []
    sooner = pol(alert_thresholds_pct=[50, 100])
    found = evaluate(limits, usage(questions_per_day=55), sooner, ["questions_per_day"])
    assert [b.level for b in found] == ["near"]


def test_spend_has_no_soft_band() -> None:
    limits = FairUseLimits(ai_spend_per_day_usd_micro=5_000_000)
    metrics = ["ai_spend_per_day"]
    found = evaluate(limits, FairUseUsage(spend_day_usd_micro=4_000_000), POLICY, metrics)
    assert [b.level for b in found] == ["near"]
    found = evaluate(limits, FairUseUsage(spend_day_usd_micro=5_000_000), POLICY, metrics)
    assert [b.level for b in found] == ["hard"]
    found = evaluate(limits, FairUseUsage(spend_unknown=True), POLICY, metrics)
    assert found[0].unknown and found[0].level == "hard"


def test_unlimited_metrics_are_never_breached() -> None:
    found = evaluate(
        FairUseLimits(), usage(questions_per_hour=10**9), POLICY, ["questions_per_hour"]
    )
    assert found == []


def test_each_breach_knows_when_it_resets() -> None:
    assert resets_at("questions_per_minute", NOW) == datetime(2026, 10, 5, 14, 21, tzinfo=UTC)
    assert resets_at("questions_per_hour", NOW) == datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
    assert resets_at("questions_per_day", NOW) == datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    assert resets_at("ai_spend_per_month", NOW) == datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    assert resets_at("storage_mb", NOW) is None
