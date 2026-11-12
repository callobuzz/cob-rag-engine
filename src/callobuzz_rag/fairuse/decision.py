"""The one decision for every answer: allow, downgrade, pause or refuse (PIP3).

`evaluate` turns limits and usage into breaches; `decide` turns the monthly cap, the billing
state, the breaches and the plan's policy into a `GateDecision`. Both are pure, so the whole
table is tested without a database. The first row that matches wins:

| # | Condition                                                   | Action                   |
|---|-------------------------------------------------------------|--------------------------|
| 1 | exempt (operator, system, Internal plan)                    | allow                    |
| 2 | the monthly question cap says no                            | refuse                   |
| 3 | billing paused, `pause_handoff`                             | pause                    |
| 4 | billing paused, `downgrade_profile`                         | downgrade, charges waived|
| 5 | billing paused, `allow`                                     | continue with row 6      |
| 6 | a spend ceiling reached, or spend could not be read         | downgrade to local, else pause |
| 7 | a hard breach, `throttle_then_stop`                         | pause                    |
| 8 | a soft breach, `throttle_then_stop` / `downgrade_only`      | downgrade, else pause    |
| 9 | a soft breach, `hard_stop`                                  | pause                    |
| 10| only `near`, or any breach with `alert_only`                | allow (alerts only)      |
| 11| nothing                                                     | allow                    |

Two safeties beyond the table. Row 4 keeps a business that already runs on the fallback profile
inside its hard lines too (a business with no balance must not be able to flood the local model);
only `alert_only` and `downgrade_only` skip that, exactly as they do in rows 7 and 8. And row 4
applies row 6 first: a reached deployer money ceiling (plan or engine rail) downgrades only to a
LOCAL target, else pauses, whatever the policy says (the owner's own cap needs no such line: the
fallback is charged nothing).
"""

import calendar
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from callobuzz_rag.billing.spend_cap import NEAR_PERCENT  # TV3 spend cap
from callobuzz_rag.core.pause import PausedBy
from callobuzz_rag.fairuse.limits import (
    SPEND_METRICS,
    Breach,
    FairUseLimits,
    FairUsePolicy,
    Metric,
    limit_of,
)
from callobuzz_rag.tiers.profiles import downgraded
from callobuzz_rag.tiers.schema import EffectiveProfile

if TYPE_CHECKING:
    from callobuzz_rag.billing.enforcement import BillingState
    from callobuzz_rag.fairuse.usage import FairUseUsage

Action = Literal["allow", "downgrade", "pause", "refuse"]
Reason = Literal[
    "ok",
    "question_cap",
    "billing_block",
    "fair_use_soft",
    "fair_use_hard",
    "spend_ceiling",
    "engine_spend_ceiling",
    "spend_unknown",
    "spend_cap",  # TV3 spend cap
    "downgrade_unavailable",
    "local_unavailable",
    "demo_limit",  # L5a demo: a visitor's or the whole demo's daily answers
]


@dataclass(frozen=True, slots=True)
class GateDecision:
    action: Action
    profile: EffectiveProfile  # what to answer with (allow: plan profile; downgrade: target)
    reasons: tuple[Reason, ...]  # most specific first; ("ok",) when allowed
    breaches: tuple[Breach, ...]
    billing: "BillingState"
    paused_by: PausedBy | None
    waive_charges: bool  # downgraded because the balance ran out: charges written waived
    retry_after_s: int | None


def period_of(metric: str) -> Literal["minute", "hour", "day", "month", "total"]:
    if metric == "questions_per_minute":
        return "minute"
    if metric == "questions_per_hour":
        return "hour"
    if metric in ("ai_spend_per_month", "spend_cap_per_month"):
        return "month"
    if metric in ("website_pages_total", "storage_mb", "concurrent_answers"):
        return "total"
    return "day"


def resets_at(metric: str, now: datetime) -> datetime | None:
    """When the counter of `metric` starts over (UTC), None for totals."""
    moment = now.astimezone(UTC)
    period = period_of(metric)
    if period == "minute":
        return moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
    if period == "hour":
        return moment.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if period == "day":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if period == "month":
        days = calendar.monthrange(moment.year, moment.month)[1]
        first = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return first + timedelta(days=days)
    return None


def near_threshold(policy: FairUsePolicy) -> int | None:
    """The lowest alert level below 100 %, where a limit starts to count as `near`."""
    below = [t for t in policy.alert_thresholds_pct if t < 100]
    return min(below) if below else None


def evaluate(
    limits: FairUseLimits,
    usage: "FairUseUsage",
    policy: FairUsePolicy,
    metrics: Iterable[Metric | str],
    *,
    now: datetime | None = None,
    spend_cap_micro: int | None = None,  # TV3 spend cap: the owner's cap, account micro units
) -> list[Breach]:
    """The breaches among `metrics`: `near` from the lowest alert level, `soft` at the limit,
    `hard` at the hard line. A spend limit has no soft band (money stops exactly at the limit,
    so it is `hard` there); a spend that could not be read is a `hard` breach marked unknown."""
    moment = now or datetime.now(UTC)
    near_at = near_threshold(policy)
    out: list[Breach] = []
    for metric in metrics:
        limit = spend_cap_micro if metric == "spend_cap_per_month" else limit_of(limits, metric)
        if limit is None or metric == "engine_ai_spend_per_day":
            continue
        reset = resets_at(metric, moment)
        if metric in SPEND_METRICS and usage.spend_unknown:
            out.append(Breach(metric, "hard", limit, 0, reset, unknown=True))  # type: ignore[arg-type]
            continue
        used = usage.used(metric)
        level: Literal["near", "soft", "hard"] | None = None
        if metric in SPEND_METRICS:
            if used >= limit:
                level = "hard"
        elif used * 100 >= limit * policy.hard_limit_pct:
            level = "hard"
        elif used >= limit:
            level = "soft"
        near_line = NEAR_PERCENT if metric == "spend_cap_per_month" else near_at  # TV3
        if level is None and near_line is not None and used * 100 >= limit * near_line:
            level = "near"
        if level is not None:
            out.append(Breach(metric, level, limit, used, reset))  # type: ignore[arg-type]
    return out


def _retry_after(breaches: Sequence[Breach], now: datetime | None = None) -> int | None:
    moment = now or datetime.now(UTC)
    waits = [
        max(1, int((b.resets_at - moment).total_seconds()))
        for b in breaches
        if b.level != "near" and b.resets_at is not None
    ]
    return max(waits) if waits else None


def _spend_reason(spend: Sequence[Breach]) -> Reason:
    """Why money stops an answer: the owner's own cap is named first when it is one of the
    reasons (TV3), then an unreadable spend, the engine rail, the plan's ceiling."""
    first = next((b for b in spend if b.metric == "spend_cap_per_month"), spend[0])
    if first.metric == "spend_cap_per_month" and not first.unknown:
        return "spend_cap"
    if first.unknown:
        return "spend_unknown"
    if first.metric == "engine_ai_spend_per_day":
        return "engine_spend_ceiling"
    return "spend_ceiling"


def decide(
    *,
    cap_blocked: bool,
    billing: "BillingState",
    breaches: Sequence[Breach],
    policy: FairUsePolicy,
    plan_profile: EffectiveProfile,
    downgrade_to: EffectiveProfile,
    exempt: bool,
    now: datetime | None = None,
) -> GateDecision:
    """The decision, pure (see the table in the module docstring)."""
    held = tuple(breaches)

    def make(
        action: Action,
        reasons: tuple[Reason, ...],
        *,
        paused_by: PausedBy | None = None,
        waive: bool = False,
    ) -> GateDecision:
        profile = downgraded(downgrade_to) if action == "downgrade" else plan_profile
        return GateDecision(
            action=action,
            profile=profile,
            reasons=reasons,
            breaches=held,
            billing=billing,
            paused_by=paused_by,
            waive_charges=waive,
            retry_after_s=_retry_after(held, now) if paused_by == "fair_use" else None,
        )

    if exempt:  # row 1
        return make("allow", ("ok",))
    if cap_blocked:  # row 2
        return make("refuse", ("question_cap",))

    can_downgrade = downgrade_to.available
    over = [b for b in held if b.level != "near"]
    hard = [b for b in over if b.level == "hard"]
    exceeded = policy.on_fair_use_exceeded

    if billing.paused:
        if policy.on_billing_block == "pause_handoff":  # row 3
            return make("pause", ("billing_block",), paused_by="billing")
        if policy.on_billing_block == "downgrade_profile":  # row 4
            if not can_downgrade:
                return make(
                    "pause", ("billing_block", "downgrade_unavailable"), paused_by="billing"
                )
            # the safety beyond the table: the deployer's money ceilings and the hard lines
            # still hold on the fallback (TV3: the owner's spending cap is no line here:
            # answers on the fallback profile are charged nothing, so they cannot spend past
            # it). Money first, exactly as row 6: only a LOCAL target may answer past a
            # reached ceiling, whatever the policy says about the other limits.
            money = [
                b for b in over if b.metric in SPEND_METRICS and b.metric != "spend_cap_per_month"
            ]
            if money and downgrade_to.config.answer_route != "local":
                return make(
                    "pause",
                    (_spend_reason(money), "downgrade_unavailable"),
                    paused_by="fair_use",
                )
            lines = [
                b
                for b in hard
                if b.metric not in SPEND_METRICS and b.metric != "spend_cap_per_month"
            ]
            if lines and exceeded in ("throttle_then_stop", "hard_stop"):
                return make("pause", ("fair_use_hard",), paused_by="fair_use")
            return make("downgrade", ("billing_block",), waive=True)
        # row 5: "allow" falls through to the fair-use rows with the plan's own profile

    spend = [b for b in over if b.metric in SPEND_METRICS]  # row 6
    if spend:
        # TV3 spend cap: the owner's own cap is named first when it is one of the reasons
        reason = _spend_reason(spend)
        local = downgrade_to.config.answer_route == "local" and can_downgrade
        if local:
            # TV3: answers past the owner's own cap are charged nothing, or it is no cap
            return make("downgrade", (reason,), waive=(reason == "spend_cap"))
        return make("pause", (reason, "downgrade_unavailable"), paused_by="fair_use")

    if over and exceeded != "alert_only":
        if exceeded == "hard_stop":  # row 9 (any breach at or past its limit)
            return make("pause", ("fair_use_hard",), paused_by="fair_use")
        if hard and exceeded == "throttle_then_stop":  # row 7
            return make("pause", ("fair_use_hard",), paused_by="fair_use")
        if exceeded in ("throttle_then_stop", "downgrade_only"):  # row 8
            if can_downgrade:
                return make("downgrade", ("fair_use_soft",))
            return make("pause", ("fair_use_soft", "downgrade_unavailable"), paused_by="fair_use")

    return make("allow", ("ok",))  # rows 10 and 11


def with_profile(decision: GateDecision, profile: EffectiveProfile) -> GateDecision:
    """The same decision answering with another profile (the gate swaps in the limits it
    applies to the profile's own concurrency)."""
    return replace(decision, profile=profile)
