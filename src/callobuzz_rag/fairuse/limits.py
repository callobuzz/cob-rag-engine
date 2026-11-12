"""Fair-use limits and policy, and how a business's effective values are found (PIP3).

`None` means unlimited everywhere. A plan stores its limits and policy as JSONB (a missing key
is the default here); a business may override any of them in `tenant_pipeline_settings`: a key
that is absent follows the plan, a key set to JSON null means unlimited (limits only).
"""

import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.schemas import StrictRequest
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.pipeline_profile import TenantPipelineSettings
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

Metric = Literal[
    "questions_per_minute",
    "questions_per_hour",
    "questions_per_day",
    "concurrent_answers",
    "website_pages_per_day",
    "website_pages_total",
    "fetches_per_day",
    "uploads_per_day",
    "storage_mb",
    "ai_spend_per_day",
    "ai_spend_per_month",
    "cob_questions_per_day",
    "engine_ai_spend_per_day",
    "spend_cap_per_month",  # TV3 spend cap: the owner's cap, never in the deployer's editor
]
# TV3 spend cap: the owner's monthly cap on usage charges. Not a `Metric` of the deployer's
# fair-use editor: the owner sets it (`billing_accounts.spend_cap_minor`), the gate enforces it.
SPEND_CAP_METRIC: Final = "spend_cap_per_month"
# /TV3 spend cap
# What the gate looks at before every answer.
ANSWER_METRICS: Final[tuple[str, ...]] = (
    "questions_per_minute",
    "questions_per_hour",
    "questions_per_day",
    "ai_spend_per_day",
    "ai_spend_per_month",
    "engine_ai_spend_per_day",
    SPEND_CAP_METRIC,  # TV3 spend cap
)
SPEND_METRICS: Final[frozenset[str]] = frozenset(
    {"ai_spend_per_day", "ai_spend_per_month", "engine_ai_spend_per_day", SPEND_CAP_METRIC}
)
# A limit's name in `FairUseLimits` for each metric (spend limits carry their unit).
LIMIT_FIELD: Final[dict[str, str]] = {
    "ai_spend_per_day": "ai_spend_per_day_usd_micro",
    "ai_spend_per_month": "ai_spend_per_month_usd_micro",
}

OnBillingBlock = Literal["downgrade_profile", "pause_handoff", "allow"]
OnExceeded = Literal["throttle_then_stop", "hard_stop", "downgrade_only", "alert_only"]
State = Literal["ok", "near", "throttled", "stopped"]


class FairUseLimits(StrictRequest):
    questions_per_minute: int | None = Field(None, ge=1, le=100_000)
    questions_per_hour: int | None = Field(None, ge=1, le=1_000_000)
    questions_per_day: int | None = Field(None, ge=1, le=10_000_000)
    concurrent_answers: int | None = Field(None, ge=1, le=200)
    website_pages_per_day: int | None = Field(None, ge=1, le=100_000)
    # = plans.website_pages_max (BIZ1); a business's override beats the plan (D9)
    website_pages_total: int | None = Field(None, ge=1, le=1_000_000)
    fetches_per_day: int | None = Field(None, ge=1, le=10_000_000)
    uploads_per_day: int | None = Field(None, ge=1, le=100_000)
    storage_mb: int | None = Field(None, ge=1, le=10_000_000)
    ai_spend_per_day_usd_micro: int | None = Field(None, ge=1)
    ai_spend_per_month_usd_micro: int | None = Field(None, ge=1)
    cob_questions_per_day: int | None = Field(None, ge=1, le=100_000)


class FairUsePolicy(StrictRequest):
    on_billing_block: OnBillingBlock = "downgrade_profile"
    on_fair_use_exceeded: OnExceeded = "throttle_then_stop"
    downgrade_profile_id: uuid.UUID | None = None  # None: the built-in Free profile
    hard_limit_pct: int = Field(150, ge=100, le=1000)  # hard line = limit x pct / 100
    alert_thresholds_pct: list[int] = Field(default_factory=lambda: [80, 100], max_length=5)

    @field_validator("alert_thresholds_pct")
    @classmethod
    def _thresholds(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("Alert levels must all be different.")
        if any(v < 1 or v > 100 for v in value):
            raise ValueError("Alert levels are percentages from 1 to 100.")
        return sorted(value)


LIMIT_FIELDS: Final[frozenset[str]] = frozenset(FairUseLimits.model_fields)
POLICY_FIELDS: Final[frozenset[str]] = frozenset(FairUsePolicy.model_fields)


def parse_limits(raw: Any) -> FairUseLimits:
    """Stored limits, re-validated on read: a key that no longer validates is dropped with a
    warning, so one bad stored value never stops a business from answering."""
    result: FairUseLimits = _lenient(FairUseLimits, raw, LIMIT_FIELDS, "limits")
    return result


def parse_policy(raw: Any) -> FairUsePolicy:
    result: FairUsePolicy = _lenient(FairUsePolicy, raw, POLICY_FIELDS, "policy")
    return result


def _lenient(model: Any, raw: Any, known: frozenset[str], what: str) -> Any:
    data = {k: v for k, v in dict(raw).items() if k in known} if isinstance(raw, Mapping) else {}
    for _ in range(len(data) + 1):
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e.get("loc") and e["loc"][0] in data}
            if not bad:
                logger.warning("Fair-use %s are invalid (%s); using the defaults", what, exc)
                return model()
            logger.warning("Fair-use %s: dropping invalid keys %s", what, sorted(bad))
            data = {k: v for k, v in data.items() if k not in bad}
    return model()  # pragma: no cover - the loop always returns


def merge_limits(plan: FairUseLimits, overrides: Mapping[str, Any] | None) -> FairUseLimits:
    """The plan's limits with a business's overrides laid over: a key that is present sets it
    (null = unlimited), a key that is absent follows the plan."""
    if not overrides:
        return plan
    merged = plan.model_dump()
    merged.update({k: v for k, v in overrides.items() if k in LIMIT_FIELDS})
    return parse_limits(merged)


def merge_policy(plan: FairUsePolicy, overrides: Mapping[str, Any] | None) -> FairUsePolicy:
    if not overrides:
        return plan
    merged = plan.model_dump()
    merged.update({k: v for k, v in overrides.items() if k in POLICY_FIELDS})
    return parse_policy(merged)


@dataclass(frozen=True, slots=True)
class Breach:
    metric: Metric
    level: Literal["near", "soft", "hard"]  # near: reached an alert level below 100 %
    limit: int
    used: int
    resets_at: datetime | None
    unknown: bool = False  # spend could not be read: fail closed


@dataclass(frozen=True, slots=True)
class EffectiveFairUse:
    limits: FairUseLimits
    policy: FairUsePolicy
    exempt: bool
    overridden: frozenset[str]
    state: State = "ok"
    tenant_name: str = ""
    plan_name: str = ""
    # TV3 spend cap: the owner's monthly cap in minor units of the account currency (None: none)
    spend_cap_minor: int | None = None
    currency: str = "INR"
    # /TV3 spend cap

    def limited(self, metrics: Iterable[str]) -> list[str]:
        """The metrics (of `metrics`) that have a limit, so only those are ever read."""
        out: list[str] = []
        for metric in metrics:
            if metric == "engine_ai_spend_per_day":
                continue  # the engine rail belongs to `rails`, not to a business's limits
            if metric == SPEND_CAP_METRIC:  # TV3 spend cap: read only when a cap is set
                if self.spend_cap_minor is not None:
                    out.append(metric)
                continue
            if limit_of(self.limits, metric) is not None:
                out.append(metric)
        return out


def limit_of(limits: FairUseLimits, metric: str) -> int | None:
    value: int | None = getattr(limits, LIMIT_FIELD.get(metric, metric), None)
    return value


def _state(value: object) -> State:
    if value == "near":
        return "near"
    if value == "throttled":
        return "throttled"
    if value == "stopped":
        return "stopped"
    return "ok"


async def effective_fair_use(session: AsyncSession, tenant_id: uuid.UUID) -> EffectiveFairUse:
    """The business's limits and policy: plan, then its overrides. One query. An unknown
    business is exempt (the route's own lookups report it)."""
    row = (
        await session.execute(
            select(
                Tenant.name,
                Tenant.is_operator,
                Tenant.is_system,
                Plan.name,
                Plan.is_internal,
                Plan.fair_use_limits,
                Plan.fair_use_policy,
                Plan.website_pages_max,
                TenantPipelineSettings.fair_use_overrides,
                TenantPipelineSettings.policy_overrides,
                TenantPipelineSettings.state,
                BillingAccount.spend_cap_minor,  # TV3 spend cap
                BillingAccount.currency,  # TV3 spend cap
            )
            .select_from(Tenant)
            .join(Plan, Plan.id == Tenant.plan_id)
            .outerjoin(TenantPipelineSettings, TenantPipelineSettings.tenant_id == Tenant.id)
            .outerjoin(BillingAccount, BillingAccount.tenant_id == Tenant.id)  # TV3 spend cap
            .where(Tenant.id == tenant_id)
        )
    ).first()
    if row is None:
        return EffectiveFairUse(FairUseLimits(), FairUsePolicy(), True, frozenset())
    name, is_operator, is_system, plan_name, is_internal = row[:5]
    plan_limits, plan_policy, pages_max = row[5], row[6], row[7]
    overrides, policy_overrides, state = row[8] or {}, row[9] or {}, row[10] or "ok"
    cap_minor, cap_currency = (row[11], row[12]) if len(row) > 12 else (None, "INR")  # TV3
    plan_side = parse_limits(plan_limits).model_copy(update={"website_pages_total": pages_max})
    return EffectiveFairUse(
        limits=merge_limits(plan_side, overrides),
        policy=merge_policy(parse_policy(plan_policy), policy_overrides),
        exempt=bool(is_operator or is_system or is_internal),
        overridden=frozenset(
            {k for k in overrides if k in LIMIT_FIELDS}
            | {k for k in policy_overrides if k in POLICY_FIELDS}
        ),
        state=_state(state),
        tenant_name=str(name),
        plan_name=str(plan_name),
        spend_cap_minor=int(cap_minor) if cap_minor is not None else None,  # TV3 spend cap
        currency=str(cap_currency or "INR"),  # TV3 spend cap
    )


def normalize_limits(raw: Any) -> dict[str, int]:
    """A plan's limits as stored: only the limits that are set. `website_pages_total` is the
    plan's `website_pages_max` (D9) and is never stored twice."""
    parsed = parse_limits(raw.model_dump() if hasattr(raw, "model_dump") else raw)
    return {
        k: v for k, v in parsed.model_dump().items() if v is not None and k != "website_pages_total"
    }


def normalize_policy(raw: Any) -> dict[str, Any]:
    """A plan's policy as stored: only what differs from the defaults (JSON-safe)."""
    parsed = parse_policy(raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw)
    defaults = FairUsePolicy().model_dump(mode="json")
    return {k: v for k, v in parsed.model_dump(mode="json").items() if v != defaults[k]}


def rule_error(exc: ValidationError) -> str:
    """The first human sentence of a validation error, for `invalid_fair_use`."""
    for err in exc.errors():
        msg = str(err.get("msg", ""))
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        loc = ".".join(str(p) for p in err.get("loc", ()))
        if err.get("type") == "extra_forbidden":
            return f"'{loc}' is not a fair-use setting."
        return f"{loc}: {msg}" if loc else msg
    return str(exc)
