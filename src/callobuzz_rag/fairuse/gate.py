"""`answer_gate`: the one hook every answering route calls before it answers (PIP3).

It replaces the `enforce_question_cap` + `enforce_billing` pair: it runs both with their own
semantics (the cap's 429 body, the admin principal never limited), reads the business's
fair-use usage (only the limited metrics), asks the pure `decide` and hands the route a
`GateDecision` that says what to answer with and whether to pause. The gate never raises except
for the monthly cap; what a pause means is the route's choice (the director's pause reply, or
`refuse_paused` on the legacy paths).

A question is counted in the minute/hour windows only when it is answered (allow or downgrade),
so a paused visitor does not push the count further. Alerts, the stored state and the decision
counters are written by a background task (`alerts.schedule`) and only when something happened.
"""

import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.caps import enforce_question_cap
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.context import is_admin_request, mark_waived
from callobuzz_rag.billing.enforcement import NOT_BILLED, BillingState, enforce_billing
from callobuzz_rag.billing.money import MICRO_PER_MINOR  # TV3 spend cap
from callobuzz_rag.fairuse import alerts, counters, rails
from callobuzz_rag.fairuse.decision import GateDecision, decide, evaluate
from callobuzz_rag.fairuse.limits import (
    ANSWER_METRICS,
    SPEND_CAP_METRIC,  # TV3 spend cap
    SPEND_METRICS,
    EffectiveFairUse,
    FairUseLimits,
    FairUsePolicy,
    effective_fair_use,
)
from callobuzz_rag.fairuse.usage import FairUseUsage, read_usage
from callobuzz_rag.tiers import profiles as tier_profiles
from callobuzz_rag.tiers.schema import EffectiveProfile

if TYPE_CHECKING:
    from fastapi import Request

    from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

Surface = Literal["api", "widget", "conversation", "eval", "cob"]


def get_redis(app: Any) -> Any:
    """The Redis client for the question windows (made on first use; tests set
    `app.state.fair_use_redis`)."""
    client = getattr(app.state, "fair_use_redis", None)
    if client is None:
        import redis.asyncio as aioredis

        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            app.state.settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        app.state.fair_use_redis = client
    return client


def _allow(profile: EffectiveProfile, billing: BillingState) -> GateDecision:
    return GateDecision("allow", profile, ("ok",), (), billing, None, False, None)


# L5a demo
async def _demo_pause(
    request: "Request", profile: EffectiveProfile, billing: BillingState
) -> GateDecision | None:
    """A pause (the polite demo reply, no model call) when this visitor or the whole demo has used
    today's answers, or when the counters cannot be read (fails closed); None to go on. Opens the
    turn's scope, so the first model call of the turn counts its answer."""
    from callobuzz_rag.demo import limits, runtime

    scope = await limits.check(request, runtime.get_redis(request.app), request.app.state.settings)
    if scope is None or scope.standing.kind == "ok":
        return None
    retry = max(1, int((scope.limiter.resets_at() - scope.limiter.now()).total_seconds()))
    return GateDecision("pause", profile, ("demo_limit",), (), billing, "demo_limit", False, retry)


def demo_limit_error(request: "Request | None" = None) -> APIError:
    """What a legacy answer path says when the demo cannot answer: the polite sentence, never a
    500. 429 when a limit is used up, 503 when the counters cannot be read."""
    from callobuzz_rag.demo import limits

    scope = limits.current_scope()
    settings = request.app.state.settings if request is not None else None
    if scope is None:
        from callobuzz_rag.core.config import get_settings

        text = limits.limiter_from(None, settings or get_settings()).text("global")
        kind = "global"
    else:
        text, kind = scope.text(), scope.standing.kind
    headers = {}
    if scope is not None:
        wait = int((scope.limiter.resets_at() - scope.limiter.now()).total_seconds())
        headers["Retry-After"] = str(max(1, wait))
    if kind == "unavailable":
        return APIError(503, text, "demo_unavailable", headers={"Retry-After": "60"})
    return APIError(429, text, "demo_limit", headers=headers)


# /L5a demo


def _unavailable(profile: EffectiveProfile) -> EffectiveProfile:
    """The downgrade target when the policy names a profile that no longer exists: a target that
    is not available, so the decision pauses instead of answering on something unintended."""
    return replace(profile, available=False)


async def _downgrade_target(
    session: AsyncSession, policy: FairUsePolicy, plan_profile: EffectiveProfile, settings: Any
) -> EffectiveProfile:
    try:
        target = await tier_profiles.load_profile(
            session, policy.downgrade_profile_id, settings=settings
        )
    except Exception:  # a target that cannot be read is a target that is not there: pause
        logger.warning("Fair-use downgrade profile could not be loaded", exc_info=True)
        target = None
    return target if target is not None else _unavailable(plan_profile)


def _own_key(profile: EffectiveProfile, payer: str) -> EffectiveProfile:
    """A business answering with its own AI key never runs on the local model (the profile only
    sets its priority and search settings), so its turn must not queue for the CPU slot."""
    if payer == "operator" or profile.config.answer_route != "local" or profile.own_key:
        return profile
    return replace(profile, own_key=True)


def _limit_concurrency(profile: EffectiveProfile, limits: FairUseLimits) -> EffectiveProfile:
    """The business's own `concurrent_answers` limit folded into the profile's per-business
    concurrency, so the answer lanes enforce it (the smaller of the two wins)."""
    cap = limits.concurrent_answers
    if cap is None:
        return profile
    current = profile.config.max_concurrency
    chosen = cap if current is None else min(current, cap)
    if chosen == current:
        return profile
    return replace(profile, config=profile.config.model_copy(update={"max_concurrency": chosen}))


async def answer_gate(
    session: AsyncSession,
    request: "Request",
    corpus: "Corpus",
    *,
    payer: str,
    surface: Surface,
    count_question: bool = True,
    enforce_cap: bool = True,
    plan_profile: EffectiveProfile | None = None,
) -> GateDecision:
    """The decision for one answer of `corpus`'s business.

    `payer` is whoever's key pays for the answer (`operator` | `byok`, from the unmodified
    resolution): the deployer's AI-spend ceilings only apply to operator-paid answers.
    `enforce_cap=False` is for a cached answer of a plan that does not count cached answers.
    `plan_profile` saves the profile query when the route already resolved it.

    Raises:
        APIError 429 `question_cap_reached`: the monthly cap, exactly as before.
    """
    tenant_id: uuid.UUID = corpus.tenant_id
    app = request.app
    settings = getattr(app.state, "settings", None)
    mark_waived(None)  # every answering route passes here, so a waiver never carries over
    if enforce_cap:
        await enforce_question_cap(
            session, request, tenant_id, widget=surface == "widget", payer=payer
        )
    billing = await enforce_billing(
        session, request, tenant_id, payer=payer, surface="eval" if surface == "eval" else "api"
    )
    profile = _own_key(
        plan_profile or await tier_profiles.plan_profile(session, tenant_id, settings=settings),
        payer,
    )
    if payer == "operator" and profile.config.answer_route == "local" and not profile.available:
        # The plan's own profile answers on the free local model and it is not ready (still
        # downloading, stopped): pause with the polite reply. The deployer's paid model must
        # never answer a free business instead (PIP2 decision).
        admin = is_admin_request(request)
        return GateDecision(
            "pause",
            profile,
            ("local_unavailable",),
            (),
            NOT_BILLED if admin else billing,
            "local_unavailable",  # no limit was reached: the callers must not say one was
            False,
            None,
        )
    # L5a demo: the demo's daily answer limits, for a visitor and for the whole demo (the shared
    # super-admin sign-in is a visitor like any other; only the admin key is exempt)
    if surface not in ("cob", "eval") and getattr(settings, "demo_mode", False) is True:
        demo_pause = await _demo_pause(request, profile, billing)
        if demo_pause is not None:
            return demo_pause
    # /L5a demo
    if is_admin_request(request):
        return _allow(profile, NOT_BILLED)

    effective = await effective_fair_use(session, tenant_id)
    if effective.exempt:
        return _allow(profile, billing)

    enabled = bool(getattr(settings, "fair_use_enabled", True))
    now = datetime.now(UTC)
    breaches = []
    # TV3 spend cap: the owner's cap is a billing control the owner set, not one of the
    # deployer's fair-use limits, so switching fair use off never switches it off
    cap_set = effective.spend_cap_minor is not None
    if enabled or cap_set:
        wanted = [
            m
            for m in effective.limited(ANSWER_METRICS)
            # the owner's cap counts every usage charge, whoever's key paid
            if payer == "operator" or m not in SPEND_METRICS or m == SPEND_CAP_METRIC
            if enabled or m == SPEND_CAP_METRIC
        ]
        if wanted:
            usage = await read_usage(session, get_redis(app), tenant_id, now, metrics=wanted)
            cap_micro = (
                effective.spend_cap_minor * MICRO_PER_MINOR
                if effective.spend_cap_minor is not None
                else None
            )
            breaches = evaluate(
                effective.limits,
                usage,
                effective.policy,
                wanted,
                now=now,
                spend_cap_micro=cap_micro,
            )
        if enabled and payer == "operator":
            engine = await rails.engine_breach(app, session, now=now)
            if engine is not None:
                breaches.append(engine)

    target = profile
    if billing.paused or any(b.level != "near" for b in breaches):
        target = await _downgrade_target(session, effective.policy, profile, settings)
        # P5 plans: the target is a stored profile (`load_profile` knows no business), so the
        # business's own-key allowance is carried over from its plan profile; without this a
        # downgrade would read as "allowed" and use a key the plan does not include.
        target = replace(target, byok_allowed=profile.byok_allowed)
        # /P5 plans
    decision = decide(
        cap_blocked=False,
        billing=billing,
        breaches=breaches,
        policy=effective.policy,
        plan_profile=profile,
        downgrade_to=target,
        exempt=False,
        now=now,
    )
    if decision.action in ("allow", "downgrade"):
        decision = replace(
            decision,
            profile=_limit_concurrency(_own_key(decision.profile, payer), effective.limits),
        )
        if decision.waive_charges:
            mark_waived("downgraded")
        if (
            enabled
            and count_question
            and (effective.limits.questions_per_minute or effective.limits.questions_per_hour)
        ):
            await counters.hit_question(get_redis(app), tenant_id, now)

    if (enabled or cap_set) and (breaches or decision.action != "allow" or effective.state != "ok"):
        alerts.schedule(app, tenant_id, decision, effective, now=now)
    return decision


# TV6 channel
async def answer_gate_for(
    session: AsyncSession,
    request: Any,
    corpus: "Corpus",
    *,
    payer: str,
    count_question: bool = True,
    plan_profile: EffectiveProfile | None = None,  # P5 plans
) -> GateDecision:
    """`answer_gate` for a message that has no HTTP request (a WhatsApp message answered by a
    worker). `request` is a `channels.base.ChannelRequest`: the same facts, never the operator.
    The order is the route's: the monthly cap (429 `question_cap_reached`, the visitor-safe
    wording), then billing, then fair use. The caller has already let a request for a person
    through before this."""
    return await answer_gate(
        session,
        request,
        corpus,
        payer=payer,
        surface="widget",
        count_question=count_question,
        plan_profile=plan_profile,  # P5 plans
    )


# /TV6 channel


def pause_args(decision: GateDecision) -> dict[str, Any]:
    """The director's pause flags for `decision`: `billing_paused` (BIL4, still set for a
    balance pause so its contract holds) and `paused_by` (`billing` | `fair_use` |
    `local_unavailable` | `demo_limit`)."""
    paused = decision.action == "pause"
    return {
        "billing_paused": paused and decision.paused_by == "billing",
        "paused_by": decision.paused_by if paused else None,
    }


def fair_use_error(decision: GateDecision) -> APIError:
    """The 429 a legacy API caller gets when fair use pauses the answer. The detail is a
    sentence the owner of the calling app can show; `metric`, `limit`, `used` and `resets_at`
    say which limit (spend limits carry no amounts: the caller never learns the deployer's cost).
    """
    worst = next((b for b in decision.breaches if b.level != "near"), None)
    headers = {}
    if decision.retry_after_s:
        headers["Retry-After"] = str(decision.retry_after_s)
    extra: dict[str, Any] = {}
    if worst is not None:
        extra["metric"] = worst.metric
        if worst.metric not in SPEND_METRICS:
            extra["limit"] = worst.limit
            extra["used"] = worst.used
        if worst.resets_at is not None:
            extra["resets_at"] = worst.resets_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return APIError(
        429,
        "This workspace has reached its usage limit for now. Answers resume automatically "
        "when the limit resets.",
        "fair_use_limit",
        headers=headers,
        **extra,
    )


LOCAL_RETRY_AFTER_S = 60


def local_unavailable_error() -> APIError:
    """The plan answers on the local model and it is not ready (still downloading, stopped):
    no limit was reached, so no usage-limit 429. The caller may retry in a minute."""
    return APIError(
        503,
        "Answers are not available right now. Please try again in a moment.",
        "local_model_unavailable",
        headers={"Retry-After": str(LOCAL_RETRY_AFTER_S)},
    )


async def refuse_paused(decision: GateDecision, request: "Request") -> None:
    """The legacy answer path has no pause reply: billing pauses answer 402 `billing_paused`
    (unchanged), fair-use pauses 429 `fair_use_limit`. Does nothing unless the decision paused."""
    if decision.action != "pause":
        return
    if decision.paused_by == "local_unavailable":
        raise local_unavailable_error()
    if decision.paused_by == "demo_limit":  # L5a demo
        raise demo_limit_error(request)
    if decision.paused_by == "fair_use":
        raise fair_use_error(decision)
    from callobuzz_rag.billing.enforcement import raise_if_paused

    await raise_if_paused(decision.billing, request)
    raise fair_use_error(decision)  # a pause the billing state no longer explains


_MONEY_REASONS = ("spend_ceiling", "engine_spend_ceiling", "spend_unknown", "spend_cap")


async def refuse_measured(decision: GateDecision, request: "Request") -> None:
    """For a run that must measure the plan's own profile (a golden run, D10): it is never
    downgraded, so what would move or stop an answer refuses it up front, except a soft
    fair-use rate downgrade (the run is not what that limit protects against).

    A pause refuses as `refuse_paused` does. A downgrade because the balance ran out is 402
    `billing_paused`, and one because a money ceiling or the owner's cap was reached is 429
    `fair_use_limit`: the run would otherwise spend on the paid model (charged, or waived to
    the deployer) after the very rule that said stop."""
    await refuse_paused(decision, request)
    if decision.action != "downgrade":
        return
    if "billing_block" in decision.reasons:
        from callobuzz_rag.billing.enforcement import raise_if_paused

        await raise_if_paused(decision.billing, request)
    if any(r in _MONEY_REASONS for r in decision.reasons):
        raise fair_use_error(decision)


async def cob_blocked(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
    enabled: bool = True,
) -> bool:
    """Whether the business has used its COB questions for today (`cob_questions_per_day`).
    COB then answers in search mode (reason `fair_use`). Never raises: a failed read lets COB
    answer. The operator's and the system workspace's own COB use is never limited."""
    if not enabled:
        return False
    try:
        effective = await effective_fair_use(session, tenant_id)
        if effective.exempt or effective.limits.cob_questions_per_day is None:
            return False
        moment = now or datetime.now(UTC)
        usage = await read_usage(
            session, None, tenant_id, moment, metrics=["cob_questions_per_day"]
        )
        return usage.cob_questions_per_day >= effective.limits.cob_questions_per_day
    except Exception:
        logger.warning("COB fair-use read failed; COB answers", exc_info=True)
        return False


__all__ = [
    "EffectiveFairUse",
    "FairUseUsage",
    "GateDecision",
    "answer_gate",
    "answer_gate_for",  # TV6 channel
    "cob_blocked",
    "demo_limit_error",  # L5a demo
    "fair_use_error",
    "get_redis",
    "refuse_measured",
    "refuse_paused",
]
