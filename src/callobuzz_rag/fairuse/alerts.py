"""What a decision leaves behind: alerts, the stored state, and the decision metrics (PIP3).

The gate decides in the answer's own transaction and then schedules `after_decision` as a
background task (a fresh session, a time budget, never touching the answer), but only when
something happened: a breach, a downgrade or pause, or a stored state that may need to
clear. An allowed answer below every alert level costs nothing here.

- **Alerts.** Crossing a level (an alert threshold, the limit, the hard line) inserts one
  `fair_use_alerts` row; the unique index makes the insert the claim, so a notice goes out once
  per crossing per period however many processes race, with no lock. Owners get an e-mail at
  every claimed level (spend only as a percentage: they never see the deployer's AI cost), the
  deployer gets the same with the customer's page, and the webhook `fair_use.limit_reached`
  fires at the limit and at the hard line. The minute window never mails (it only drives the
  decision). Mail and webhook go out only after the commit.
- **State.** `tenant_pipeline_settings.state` (`ok`, `near`, `throttled`, `stopped`) is moved
  by compare-and-set; `fair_use.throttled`, `fair_use.stopped` and `fair_use.resumed` are
  audited once per transition.
- **Metrics.** Every downgrade and pause bumps a `decisions_*` day counter here, never in the
  answer's transaction.
- **Limits that are not answers** (uploads, storage, website reads and pages, COB questions) have
  no decision to hang an alert on: the code that spends them calls `schedule_usage`, which
  evaluates just those metrics in a background task (`after_usage`) and claims, mails and
  webhooks exactly as above. Mails go out first; webhooks are delivered afterwards, each on its
  own, so a slow subscriber cannot hold back a notice.
- **The engine rail** (`engine_ai_spend_per_day`) alerts the deployer only, with tenant NULL.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.billing.money import MICRO_PER_MINOR  # TV3 spend cap
from callobuzz_rag.billing.spend_cap import NEAR_PERCENT  # TV3 spend cap
from callobuzz_rag.core.events import WebhookEvent
from callobuzz_rag.db.models.fair_use import FairUseAlert
from callobuzz_rag.db.models.pipeline_profile import TenantPipelineSettings
from callobuzz_rag.fairuse import counters
from callobuzz_rag.fairuse.decision import GateDecision
from callobuzz_rag.fairuse.limits import SPEND_METRICS, Breach, EffectiveFairUse, FairUsePolicy

logger = logging.getLogger(__name__)

ALERT_EMAIL_EXPIRES_S: Final = 24 * 3600
SYSTEM_ACTOR: Final = ("system", None, "system")
ENGINE_METRIC: Final = "engine_ai_spend_per_day"
UPLOAD_METRICS: Final = ("uploads_per_day", "storage_mb")
WEBSITE_METRICS: Final = ("fetches_per_day", "website_pages_per_day", "website_pages_total")
WEBHOOK_BUDGET_S: Final = 600.0  # a webhook's retries may take minutes; never forever

Dispatch = Callable[[WebhookEvent, dict[str, Any]], Awaitable[Any]]
EnqueueEmail = Callable[..., Any]

METRIC_LABELS: Final[dict[str, str]] = {
    "questions_per_minute": "questions per minute",
    "questions_per_hour": "questions this hour",
    "questions_per_day": "questions today",
    "concurrent_answers": "answers at the same time",
    "website_pages_per_day": "website pages read today",
    "website_pages_total": "website pages",
    "fetches_per_day": "website reads today",
    "uploads_per_day": "uploads today",
    "storage_mb": "storage",
    "ai_spend_per_day": "AI usage today",
    "ai_spend_per_month": "AI usage this month",
    "cob_questions_per_day": "questions to COB today",
    ENGINE_METRIC: "AI spend today",
    "spend_cap_per_month": "monthly spending cap",  # TV3 spend cap
}


def period_key(metric: str, now: datetime) -> str:
    """The period a crossing belongs to: one notice per level per period."""
    moment = now.astimezone(UTC)
    if metric == "questions_per_hour":
        return moment.strftime("%Y-%m-%dT%H")
    if metric in ("ai_spend_per_month", "spend_cap_per_month"):
        return moment.strftime("%Y-%m")
    if metric in ("website_pages_total", "storage_mb", "concurrent_answers"):
        return "total"
    return moment.strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class Crossing:
    metric: str
    level_pct: int
    kind: Literal["near", "soft", "hard"]
    breach: Breach


def crossings(breach: Breach, policy: FairUsePolicy) -> list[Crossing]:
    """The levels this breach has reached: every alert threshold at or below its percentage,
    the limit itself (100) and, past it, the hard line."""
    if breach.unknown or breach.limit <= 0 or breach.metric == "questions_per_minute":
        return []
    pct = breach.used * 100 // breach.limit
    # TV3 spend cap: the owner's cap always alerts at 80 % and at the cap itself
    thresholds = (
        (NEAR_PERCENT,) if breach.metric == "spend_cap_per_month" else policy.alert_thresholds_pct
    )
    levels = {t for t in thresholds if pct >= t}
    if breach.level in ("soft", "hard"):
        levels.add(100)
    hard_line = 100 if breach.metric in SPEND_METRICS else policy.hard_limit_pct
    if breach.level == "hard":
        levels.add(hard_line)
    out: list[Crossing] = []
    for level in sorted(levels):
        if level >= hard_line and breach.level == "hard":
            kind: Literal["near", "soft", "hard"] = "hard"
        elif level >= 100:
            kind = "soft"
        else:
            kind = "near"
        out.append(Crossing(breach.metric, level, kind, breach))
    return out


def state_of(
    decision: GateDecision, breaches: Sequence[Breach], current: str
) -> Literal["ok", "near", "throttled", "stopped"]:
    """The fair-use state a decision implies. Billing pauses and downgrades are not fair use,
    and neither is the engine's own spend rail (it is the deployer's limit, not the business's):
    that decision leaves the business's state as it is."""
    if decision.reasons[0] in ("engine_spend_ceiling", "spend_cap"):  # TV3: not fair use
        return current if current in ("ok", "near", "throttled", "stopped") else "ok"  # type: ignore[return-value]
    if decision.paused_by == "fair_use":
        return "stopped"
    if decision.action == "downgrade" and decision.reasons[0] != "billing_block":
        return "throttled"
    return "near" if breaches else "ok"


@dataclass(slots=True)
class Notice:
    tenant_id: uuid.UUID | None
    tenant_name: str
    crossing: Crossing
    key: str
    action: str
    owners: list[str] = field(default_factory=list)
    currency: str = "INR"  # TV3 spend cap: how the owner reads the amounts of the cap


# What a limit that is not about answers does once it is reached (the visitor sees nothing)
_USAGE_EFFECTS: Final[dict[str, str]] = {
    "uploads_per_day": "New uploads are refused{when}.",
    "storage_mb": "New uploads are refused until some documents are removed or the plan has "
    "more room.",
    "fetches_per_day": "Reading your website pauses{when} and carries on by itself.",
    "website_pages_per_day": "Reading your website pauses{when} and carries on by itself.",
    "website_pages_total": "No more website pages are added until some are removed or the plan "
    "has more room.",
    "cob_questions_per_day": "COB answers with search results only{when}.",
}


def what_happens(
    notice_kind: str,
    action: str,
    resets: datetime | None,
    spend: bool,
    metric: str = "",
) -> str:
    """One plain sentence for the mail: what the visitor of the business sees from now on."""
    when = f" until {resets.strftime('%d %b, %H:%M')} UTC" if resets is not None else ""
    if notice_kind == "near":
        return "Nothing changes yet. This is a heads-up so you can plan."
    if metric in _USAGE_EFFECTS:
        return _USAGE_EFFECTS[metric].format(when=when)
    if action == "pause":
        return (
            "Your assistant now shows your contact options instead of answering"
            f"{when}. Visitors are never shown an error."
        )
    if action == "downgrade":
        return f"Answers continue, but more slowly{when}."
    return "Nothing changes for your visitors; this is a notice only."


async def _claim(
    session: AsyncSession, tenant_id: uuid.UUID | None, crossing: Crossing, key: str
) -> bool:
    """True when this process is the first to see `crossing` in its period."""
    stmt = (
        insert(FairUseAlert)
        .values(
            tenant_id=tenant_id,
            metric=crossing.metric,
            period_key=key,
            level_pct=crossing.level_pct,
        )
        # any unique violation: the NULLS NOT DISTINCT index also holds for the engine rail
        .on_conflict_do_nothing()
        .returning(FairUseAlert.id)
    )
    return (await session.execute(stmt)).first() is not None


async def _move_state(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    old: str,
    new: str,
    now: datetime,
) -> bool:
    """Compare-and-set the stored state; True when this call made the change."""
    await session.execute(
        insert(TenantPipelineSettings)
        .values(tenant_id=tenant_id)
        .on_conflict_do_nothing(index_elements=["tenant_id"])
    )
    result = await session.execute(
        update(TenantPipelineSettings)
        .where(TenantPipelineSettings.tenant_id == tenant_id, TenantPipelineSettings.state == old)
        .values(state=new, state_since=now)
    )
    return bool(getattr(result, "rowcount", 0))


async def after_decision(
    session_factory: Any,
    tenant_id: uuid.UUID,
    decision: GateDecision,
    effective: EffectiveFairUse,
    *,
    now: datetime | None = None,
    settings: Any = None,
    enqueue: EnqueueEmail | None = None,
    dispatch: Dispatch | None = None,
    webhooks: bool = True,
) -> list[Notice]:
    """Alerts, state and metrics for one decision. Never raises into the answer: the caller
    runs it as a background task. Returns the notices it sent (for tests).

    `webhooks=False` leaves the webhook to the caller (`deliver_webhooks`), which runs it
    outside the time budget of this write: every mail is already queued when this returns."""
    moment = now or datetime.now(UTC)
    tenant_breaches = [b for b in decision.breaches if b.metric != ENGINE_METRIC]
    engine = [b for b in decision.breaches if b.metric == ENGINE_METRIC]
    notices: list[Notice] = []
    async with session_factory() as session:
        if decision.action in ("downgrade", "pause"):
            metric = (
                "decisions_downgraded" if decision.action == "downgrade" else "decisions_paused"
            )
            await counters.bump(session, tenant_id, metric, 1, counters.utc_day(moment))

        for breach in tenant_breaches:
            for crossing in crossings(breach, effective.policy):
                key = period_key(crossing.metric, moment)
                if await _claim(session, tenant_id, crossing, key):
                    notices.append(
                        Notice(
                            tenant_id,
                            effective.tenant_name,
                            crossing,
                            key,
                            _action_word(decision),
                            currency=effective.currency,  # TV3 spend cap
                        )
                    )
                    if crossing.metric == "spend_cap_per_month" and crossing.kind == "hard":
                        await _audit_cap_reached(session, tenant_id, effective, crossing, key)

        new_state = state_of(decision, tenant_breaches, effective.state)
        if new_state != effective.state and await _move_state(
            session, tenant_id, effective.state, new_state, moment
        ):
            await _audit_transition(session, tenant_id, effective, decision, new_state, moment)

        engine_notices: list[Notice] = []
        engine_pct = await _engine_alert_pct(session_factory) if engine else 100
        for breach in engine:
            policy = FairUsePolicy(alert_thresholds_pct=[engine_pct])
            for crossing in crossings(breach, policy):
                key = period_key(ENGINE_METRIC, moment)
                if await _claim(session, None, crossing, key):
                    engine_notices.append(Notice(None, "", crossing, key, _action_word(decision)))
                    if crossing.kind == "hard":
                        await _audit_engine_spend(session, breach, key)
        if notices:
            from callobuzz_rag.accounts.lifecycle import owner_emails

            owners = await owner_emails(session, tenant_id)
            for notice in notices:
                notice.owners = owners
        await session.commit()
    notices += engine_notices
    if notices:
        await _mail(notices, settings, enqueue)
        if webhooks:
            await deliver_webhooks(session_factory, notices, settings, dispatch)
    return notices


async def after_usage(
    session_factory: Any,
    tenant_id: uuid.UUID,
    metrics: Sequence[str],
    *,
    now: datetime | None = None,
    settings: Any = None,
    enqueue: EnqueueEmail | None = None,
    dispatch: Dispatch | None = None,
    webhooks: bool = True,
) -> list[Notice]:
    """Alerts for limits that are not about answering (uploads, storage, website reads and
    pages, COB questions): reads the business's usage of `metrics`, claims every level it has
    reached in this period and mails it, with the webhook at the limit. No decision, no stored
    state: nothing here touches the visitors. Never raises into the caller."""
    from callobuzz_rag.fairuse.decision import evaluate
    from callobuzz_rag.fairuse.limits import effective_fair_use
    from callobuzz_rag.fairuse.usage import read_usage

    moment = now or datetime.now(UTC)
    notices: list[Notice] = []
    try:
        async with session_factory() as session:
            effective = await effective_fair_use(session, tenant_id)
            wanted = effective.limited(list(metrics))
            if effective.exempt or not wanted:
                return []
            usage = await read_usage(session, None, tenant_id, moment, metrics=wanted)
            for breach in evaluate(effective.limits, usage, effective.policy, wanted, now=moment):
                for crossing in crossings(breach, effective.policy):
                    key = period_key(crossing.metric, moment)
                    if await _claim(session, tenant_id, crossing, key):
                        notices.append(
                            Notice(tenant_id, effective.tenant_name, crossing, key, "alert")
                        )
            if notices:
                from callobuzz_rag.accounts.lifecycle import owner_emails

                owners = await owner_emails(session, tenant_id)
                for notice in notices:
                    notice.owners = owners
            await session.commit()
        if notices:
            await _mail(notices, settings, enqueue)
            if webhooks:
                await deliver_webhooks(session_factory, notices, settings, dispatch)
    except Exception:
        logger.warning("Fair-use usage alert failed", exc_info=True)
    return notices


async def _engine_alert_pct(session_factory: Any) -> int:
    """The level (percent of the engine's daily spend ceiling) the deployer is warned at."""
    from callobuzz_rag.accounts.settings_store import get_engine_settings

    try:
        return int((await get_engine_settings(session_factory)).pipelines.engine_spend_alert_pct)
    except Exception:
        return 80


async def _audit_cap_reached(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    effective: EffectiveFairUse,
    crossing: Crossing,
    month: str,
) -> None:
    """The owner's monthly spending cap was reached (system actor, once per month)."""
    await record_audit(
        session,
        None,
        "billing.spend_cap_reached",
        tenant_id=tenant_id,
        tenant_label=effective.tenant_name,
        target_type="tenant",
        target_id=str(tenant_id),
        details={
            "cap_minor": effective.spend_cap_minor,
            "used_minor": crossing.breach.used // MICRO_PER_MINOR,
            "month": month,
        },
        actor=SYSTEM_ACTOR,
    )


def _action_word(decision: GateDecision) -> str:
    return {"downgrade": "downgrade", "pause": "pause"}.get(decision.action, "alert")


async def _audit_engine_spend(session: AsyncSession, breach: Breach, day: str) -> None:
    """The deployer's daily AI spend ceiling was reached (system actor, once per day)."""
    await record_audit(
        session,
        None,
        "fair_use.engine_spend_reached",
        details={"limit_usd_micro": breach.limit, "used_usd_micro": breach.used, "day": day},
        actor=SYSTEM_ACTOR,
    )


async def _audit_transition(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    effective: EffectiveFairUse,
    decision: GateDecision,
    new_state: str,
    moment: datetime,
) -> None:
    action = {
        "throttled": "fair_use.throttled",
        "stopped": "fair_use.stopped",
        "ok": "fair_use.resumed",
    }.get(new_state)
    if action is None:
        return  # ok -> near and back are not audited
    if new_state == "ok" and effective.state == "near":
        return
    worst = next((b for b in decision.breaches if b.level != "near"), None)
    await record_audit(
        session,
        None,
        action,
        tenant_id=tenant_id,
        tenant_label=effective.tenant_name,
        target_type="tenant",
        target_id=str(tenant_id),
        details={
            "metric": worst.metric if worst is not None else None,
            "limit": worst.limit
            if worst is not None and worst.metric not in SPEND_METRICS
            else None,
            "used": worst.used if worst is not None and worst.metric not in SPEND_METRICS else None,
            "period_key": period_key(worst.metric, moment) if worst is not None else None,
            "profile": decision.profile.name,
        },
        actor=SYSTEM_ACTOR,
    )


# ------------------------------------------------------------------ notifying


def _numbers(c: Crossing, currency: str = "INR") -> tuple[str, str]:
    """`(used, limit)` as the owner reads them: AI spend only as a percentage, the owner's own
    spending cap in money (it is their own charges)."""
    b = c.breach
    if b.metric == "spend_cap_per_month":  # TV3 spend cap
        from callobuzz_rag.billing.notice import money_text

        return (
            money_text(b.used // MICRO_PER_MINOR, currency),
            money_text(b.limit // MICRO_PER_MINOR, currency),
        )
    if b.metric in SPEND_METRICS:
        return f"{min(b.used * 100 // max(b.limit, 1), 999)}%", "100%"
    return f"{b.used:,}", f"{b.limit:,}"


async def _mail(
    notices: Sequence[Notice],
    settings: Any,
    enqueue: EnqueueEmail | None,
) -> None:
    """Queue the mail of every committed crossing, all of them before any webhook. Every step
    is best effort."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.core.config import get_settings

    cfg = settings or get_settings()
    if enqueue is None:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        enqueue = enqueue_email
    operator = (cfg.superadmin_email or "").strip()
    for n in notices:
        c = n.crossing
        used, limit = _numbers(c, n.currency)
        resets = c.breach.resets_at
        spend = c.metric in SPEND_METRICS
        happens = what_happens(c.kind, n.action, resets, spend, c.metric)
        if n.tenant_id is None:
            targets = [(operator, "engine_spend_alert", _engine_context(c, cfg, dashboard_link))]
        else:
            owner_ctx = {
                "tenant_name": n.tenant_name,
                "metric_label": METRIC_LABELS.get(c.metric, c.metric),
                "level_pct": c.level_pct,
                "used": used,
                "limit": limit,
                "what_happens": happens,
                "resets_at": resets.strftime("%d %b %Y, %H:%M UTC") if resets else "",
                "plan_url": dashboard_link(cfg.dashboard_url, "/plan"),
            }
            admin_ctx = {
                "tenant_name": n.tenant_name,
                "metric_label": METRIC_LABELS.get(c.metric, c.metric),
                "level_pct": c.level_pct,
                "used": _admin_used(c),
                "limit": _admin_limit(c),
                "what_happens": happens,
                "customer_url": dashboard_link(cfg.dashboard_url, f"/customers/{n.tenant_id}"),
            }
            targets = [(to, "fair_use_alert", owner_ctx) for to in n.owners]
            if operator and c.metric != "spend_cap_per_month":  # TV3: the cap is the owner's
                targets.append((operator, "fair_use_alert_admin", admin_ctx))
        for to, template, ctx in targets:
            if not to:
                continue
            try:
                enqueue(template, to, ctx, expires_s=ALERT_EMAIL_EXPIRES_S)
            except Exception as exc:
                logger.error("Fair-use notice mail not queued (%s)", type(exc).__name__)


async def deliver_webhooks(
    session_factory: Any,
    notices: Sequence[Notice],
    settings: Any = None,
    dispatch: Dispatch | None = None,
) -> None:
    """The `fair_use.limit_reached` webhook of the committed crossings at the limit or past it.
    Each notice goes on its own, all at the same time, and a failure of one never reaches
    another (a dispatch may sleep through retries). Never raises."""
    from callobuzz_rag.core.config import get_settings

    due = [n for n in notices if n.crossing.kind in ("soft", "hard")]
    due = [n for n in due if n.crossing.metric != "spend_cap_per_month"]
    if not due:
        return
    if dispatch is None:
        from callobuzz_rag.core.webhooks import WebhookDispatcher

        dispatch = WebhookDispatcher(settings or get_settings(), session_factory).dispatch
    send = dispatch

    async def one(n: Notice) -> None:
        c = n.crossing
        try:
            await send(
                WebhookEvent.FAIR_USE_LIMIT_REACHED,
                {
                    "tenant_id": str(n.tenant_id) if n.tenant_id else None,
                    "tenant_name": n.tenant_name or None,
                    "metric": c.metric,
                    "level": "hard" if c.kind == "hard" else "soft",
                    "limit": c.breach.limit,
                    "used": c.breach.used,
                    "period_key": n.key,
                    "action": n.action,
                },
            )
        except Exception as exc:
            logger.error("Fair-use notice webhook failed (%s)", type(exc).__name__)

    await asyncio.gather(*(one(n) for n in due))


def _admin_used(c: Crossing) -> str:
    b = c.breach
    if b.metric in SPEND_METRICS:
        return f"${b.used / 1_000_000:,.2f}"
    return f"{b.used:,}"


def _admin_limit(c: Crossing) -> str:
    b = c.breach
    if b.metric in SPEND_METRICS:
        return f"${b.limit / 1_000_000:,.2f}"
    return f"{b.limit:,}"


def _engine_context(c: Crossing, cfg: Any, link: Callable[..., str]) -> Mapping[str, Any]:
    return {
        "level_pct": c.level_pct,
        "used": _admin_used(c),
        "limit": _admin_limit(c),
        "costs_url": link(cfg.dashboard_url, "/costs"),
    }


# ------------------------------------------------------------------ scheduling

_busy_tasks: set[asyncio.Task[None]] = set()


async def note_busy(session_factory: Any, tenant_id: uuid.UUID) -> None:
    """Count one question that waited too long for an answer slot (`decisions_busy`), in a
    session of its own. Best effort: it never touches the visitor's reply."""
    try:
        async with session_factory() as session:
            await counters.bump(session, tenant_id, "decisions_busy", 1)
            await session.commit()
    except Exception:
        logger.warning("Could not count a busy reply", exc_info=True)


async def _busy(session_factory: Any, tenant_id: uuid.UUID, redis: Any) -> None:
    if session_factory is not None:
        await note_busy(session_factory, tenant_id)
    if redis is not None:
        # the question was never answered: it leaves the per-minute and per-hour windows
        await counters.unhit_question(redis, tenant_id)


def schedule_busy(session_factory: Any, tenant_id: uuid.UUID, *, redis: Any = None) -> None:
    """`note_busy` in the background (the answer path never waits for it). With `redis` (the
    client of the question windows) the question is also taken out of them: only an answered
    question counts."""
    if session_factory is None and redis is None:
        return
    try:
        task = asyncio.get_running_loop().create_task(_busy(session_factory, tenant_id, redis))
        _busy_tasks.add(task)
        task.add_done_callback(_busy_tasks.discard)
    except RuntimeError:  # no running loop: nothing to schedule on
        return


def schedule(
    app: Any,
    tenant_id: uuid.UUID,
    decision: GateDecision,
    effective: EffectiveFairUse,
    *,
    now: datetime | None = None,
) -> None:
    """Run `after_decision` in the background with its own time budget; never raises. The task
    set lives on the app so tasks are not garbage-collected mid-flight."""
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        return
    settings = getattr(app.state, "settings", None)
    timeout = float(getattr(settings, "fair_use_alert_task_timeout_seconds", 10) or 10)

    async def run() -> None:
        notices: Any = None
        try:  # the write and every mail are time-boxed ...
            notices = await asyncio.wait_for(
                after_decision(
                    factory,
                    tenant_id,
                    decision,
                    effective,
                    now=now,
                    settings=settings,
                    webhooks=False,
                ),
                timeout,
            )
        except Exception:
            logger.warning("Fair-use alert task failed", exc_info=True)
        if notices:  # ... a webhook may sleep through its retries and has its own, longer bound
            try:
                await asyncio.wait_for(
                    deliver_webhooks(factory, notices, settings), WEBHOOK_BUDGET_S
                )
            except Exception:
                logger.warning("Fair-use webhook task failed", exc_info=True)

    try:
        tasks: set[asyncio.Task[None]] = getattr(app.state, "fair_use_tasks", None) or set()
        app.state.fair_use_tasks = tasks
        task = asyncio.get_running_loop().create_task(run())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    except Exception:
        logger.warning("Fair-use alert task not scheduled", exc_info=True)


async def drain(app: Any, timeout: float = 30.0) -> None:
    """Wait, up to `timeout` seconds, for the alert tasks `schedule` started on `app`. A worker
    that answers one message per `asyncio.run` calls this before it closes its database engine,
    or the loop's shutdown would cut a notice off after its claim was committed (a claim is
    never made again). Never raises."""
    tasks = {t for t in getattr(app.state, "fair_use_tasks", None) or () if not t.done()}
    if not tasks:
        return
    try:
        _, pending = await asyncio.wait(tasks, timeout=timeout)
    except Exception:
        logger.warning("Fair-use alert tasks could not be awaited", exc_info=True)
        return
    if pending:
        logger.warning(
            "%d fair-use alert task(s) still running after %.0f s", len(pending), timeout
        )


_usage_tasks: set[asyncio.Task[Any]] = set()


def schedule_usage(
    session_factory: Any, tenant_id: uuid.UUID, metrics: Sequence[str], settings: Any = None
) -> None:
    """`after_usage` in the background: the code that spent a limit that is not about answers
    (an upload, a website read, a COB question) never waits for the alert. Never raises."""
    if session_factory is None:
        return
    timeout = float(getattr(settings, "fair_use_alert_task_timeout_seconds", 10) or 10)

    async def run() -> None:
        notices: Any = None
        try:
            notices = await asyncio.wait_for(
                after_usage(session_factory, tenant_id, metrics, settings=settings, webhooks=False),
                timeout,
            )
        except Exception:
            logger.warning("Fair-use usage alert task failed", exc_info=True)
        if notices:
            try:
                await asyncio.wait_for(
                    deliver_webhooks(session_factory, notices, settings), WEBHOOK_BUDGET_S
                )
            except Exception:
                logger.warning("Fair-use usage webhook task failed", exc_info=True)

    try:
        task = asyncio.get_running_loop().create_task(run())
        _usage_tasks.add(task)
        task.add_done_callback(_usage_tasks.discard)
    except RuntimeError:  # no running loop: nothing to schedule on
        return


def schedule_upload_alerts(app: Any, tenant_id: uuid.UUID) -> None:
    """`schedule_usage` for the day's uploads and the storage, from a route that has just taken
    (or refused) an upload. Nothing when fair use is switched off. Never raises."""
    state = getattr(app, "state", None)
    settings = getattr(state, "settings", None)
    if not getattr(settings, "fair_use_enabled", True):
        return
    schedule_usage(getattr(state, "session_factory", None), tenant_id, UPLOAD_METRICS, settings)


async def drain_usage(timeout: float = 30.0) -> None:
    """`drain` for the tasks of `schedule_usage` (a worker task calls it before its engine
    closes)."""
    tasks = {t for t in _usage_tasks if not t.done()}
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
