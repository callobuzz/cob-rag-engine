"""Fair-use API schemas (PIP3): the deployer's view and overrides, and the owner's read-only view."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from callobuzz_rag.core.schemas import StrictRequest
from callobuzz_rag.fairuse.limits import FairUseLimits, FairUsePolicy

State = Literal["ok", "near", "throttled", "stopped"]
Level = Literal["ok", "near", "soft", "hard"]


class BreachOut(BaseModel):
    metric: str
    level: Literal["near", "soft", "hard"]
    limit: int
    used: int
    resets_at: datetime | None


class MetricUsageOut(BaseModel):
    metric: str
    label: str
    used: int = Field(description="AI spend is in USD micro (millionths of a dollar)")
    limit: int
    pct: int
    level: Level
    resets_at: datetime | None = Field(description="When the count starts over (UTC)")


class DecisionsToday(BaseModel):
    downgraded: int
    paused: int
    busy: int


class FairUseAdminView(BaseModel):
    tenant_id: UUID
    name: str
    plan: str
    limits: FairUseLimits = Field(description="Effective: the plan's, then this business's own")
    policy: FairUsePolicy = Field(description="Effective: the plan's, then this business's own")
    overridden: list[str] = Field(description="Fields this business sets itself (not the plan)")
    usage: list[MetricUsageOut]
    state: State
    state_since: datetime | None
    decisions_today: DecisionsToday
    exempt: bool = Field(
        False,
        description="Never limited (operator, system or Internal-plan business): the gate ignores it",
    )


class FairUseRow(BaseModel):
    tenant_id: UUID
    name: str
    plan: str
    profile_label: str
    state: State
    exempt: bool = Field(False, description="Never limited; the gate ignores this business")
    worst: BreachOut | None = Field(
        description="The most serious limit right now; only worked out for a business whose "
        "state is not ok"
    )


class FairUseListResponse(BaseModel):
    items: list[FairUseRow]
    total: int


class FairUseOverridesRequest(StrictRequest):
    """Fields of one business that differ from its plan. `limits` and `policy` set a value (a
    limit set to null means unlimited); `clear` goes back to following the plan."""

    limits: dict[str, int | None] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)


class OwnerMetric(BaseModel):
    metric: str
    label: str
    used: int | None = Field(description="null for AI usage: owners only see the share")
    limit: int | None = Field(description="null for AI usage: owners only see the share")
    pct: int
    level: Level
    resets_at: datetime | None


class FairUseOwnerView(BaseModel):
    answer_speed: str = Field(description="What the workspace's answers run on: Economy, ...")
    state: State
    answers_paused: Literal["local_unavailable"] | None = Field(
        None,
        description=(
            "Why every visitor currently sees the pause message although no limit was passed: "
            "local_unavailable = the plan answers on the local model and it is not ready"
        ),
    )
    metrics: list[OwnerMetric]
    billing_block: Literal["downgrade_profile", "pause_handoff", "allow"] = Field(
        "downgrade_profile",
        description=(
            "What happens to the workspace's answers when its balance runs out: "
            "downgrade_profile (they continue on the slower, free profile), pause_handoff "
            "(visitors see the pause message) or allow. The dashboard words its balance "
            "banner from it."
        ),
    )


class FairUseTile(BaseModel):
    """The Home page's heads-up: shown only when a limit is close or passed."""

    state: State
    worst_metric: str | None = None
    pct: int | None = None
    resets_at: datetime | None = None
