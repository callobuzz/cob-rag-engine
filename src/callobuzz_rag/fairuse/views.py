"""What the fair-use screens show for one business (PIP3).

The deployer sees every number; the owner sees the same rows but never an AI spend amount (D13:
the customer never sees the deployer's cost, only how much of the allowance is used). The rows
come from the same readers the gate uses, so a screen can never disagree with what the engine
does.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.pipeline_profile import TenantPipelineSettings
from callobuzz_rag.fairuse import counters
from callobuzz_rag.fairuse.alerts import METRIC_LABELS
from callobuzz_rag.fairuse.decision import evaluate, resets_at
from callobuzz_rag.fairuse.gate import get_redis
from callobuzz_rag.fairuse.limits import (
    SPEND_METRICS,
    Breach,
    EffectiveFairUse,
    effective_fair_use,
    limit_of,
)
from callobuzz_rag.fairuse.usage import read_usage

logger = logging.getLogger(__name__)

DISPLAY_METRICS = (
    "questions_per_minute",
    "questions_per_hour",
    "questions_per_day",
    "website_pages_per_day",
    "website_pages_total",
    "fetches_per_day",
    "uploads_per_day",
    "storage_mb",
    "ai_spend_per_day",
    "ai_spend_per_month",
    "cob_questions_per_day",
)

Level = Literal["ok", "near", "soft", "hard"]


@dataclass(frozen=True, slots=True)
class MetricRow:
    metric: str
    label: str
    used: int
    limit: int
    pct: int
    level: Level
    resets_at: datetime | None

    @property
    def is_spend(self) -> bool:
        return self.metric in SPEND_METRICS


async def metric_rows(
    session: AsyncSession,
    app: Any,
    tenant_id: uuid.UUID,
    effective: EffectiveFairUse,
    *,
    now: datetime | None = None,
) -> tuple[list[MetricRow], list[Breach]]:
    """The business's limited metrics with their usage, and the breaches among them."""
    moment = now or datetime.now(UTC)
    wanted = effective.limited(DISPLAY_METRICS)
    if not wanted:
        return [], []
    try:
        redis = get_redis(app)
    except Exception:
        redis = None
    usage = await read_usage(session, redis, tenant_id, moment, metrics=wanted)
    breaches = evaluate(effective.limits, usage, effective.policy, wanted, now=moment)
    by_metric: dict[str, Breach] = {b.metric: b for b in breaches}
    rows: list[MetricRow] = []
    for metric in wanted:
        limit = limit_of(effective.limits, metric)
        if limit is None:
            continue
        used = usage.used(metric)
        breach = by_metric.get(metric)
        rows.append(
            MetricRow(
                metric=metric,
                label=METRIC_LABELS.get(metric, metric),
                used=used,
                limit=limit,
                pct=min(used * 100 // max(limit, 1), 999),
                level=breach.level if breach is not None else "ok",
                resets_at=resets_at(metric, moment),
            )
        )
    return rows, breaches


def live_state(stored: str, rows: Sequence[MetricRow]) -> str:
    """The state a screen shows. A decision stores `near`, `throttled` or `stopped` (answers
    only); a limit that no decision moves (uploads, storage, website reads, COB questions) that
    is close or reached lifts a stored `ok` to `near`, so the business is not shown as fine with
    no limit named. A stored state a decision made is never lowered."""
    if stored == "ok" and any(r.level != "ok" for r in rows):
        return "near"
    return stored


async def settings_row(
    session: AsyncSession, tenant_id: uuid.UUID
) -> TenantPipelineSettings | None:
    return (
        await session.execute(
            select(TenantPipelineSettings).where(TenantPipelineSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()


async def decisions_today(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    values = await counters.read_counters(
        session,
        tenant_id,
        counters.utc_day(),
        ["decisions_downgraded", "decisions_paused", "decisions_busy"],
    )
    return {
        "downgraded": values["decisions_downgraded"],
        "paused": values["decisions_paused"],
        "busy": values["decisions_busy"],
    }


async def home_tile(
    session: AsyncSession, app: Any, tenant_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[str, str, int, datetime | None] | None:
    """`(state, metric, pct, resets_at)` for the Home page when a limit is near or passed, else None.
    Never raises: a heads-up must not stop the page."""
    try:
        effective = await effective_fair_use(session, tenant_id)
        if effective.exempt:
            return None
        rows, _ = await metric_rows(session, app, tenant_id, effective, now=now)
        close = [r for r in rows if r.level != "ok"]
        if not close:
            return None
        worst = max(close, key=lambda r: r.pct)
        return live_state(effective.state, rows), worst.metric, worst.pct, worst.resets_at
    except Exception:
        logger.warning("Fair-use heads-up for Home failed", exc_info=True)
        return None


def worst_breach(breaches: list[Breach]) -> Breach | None:
    """The most serious breach (hard, then soft, then near; the larger share of its limit)."""
    order = {"hard": 3, "soft": 2, "near": 1}
    if not breaches:
        return None
    return max(breaches, key=lambda b: (order[b.level], b.used / max(b.limit, 1)))
