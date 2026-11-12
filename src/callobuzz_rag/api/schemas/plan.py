"""Plan API schemas (accounts A6): the plan catalogue, and a tenant's plan and usage."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from callobuzz_rag.api.features import DashboardFeatures, parse_features  # BIZ3 plan
from callobuzz_rag.channels.policy import ChannelsPolicy, parse_channels  # TV6 channel
from callobuzz_rag.fairuse.limits import (  # PIP3 fair use
    FairUseLimits,
    FairUsePolicy,
    parse_limits,
    parse_policy,
)

_CAP = "null = unlimited"
_ENGINE = "null = the engine default (a plan can only set a lower cap)"
_PAGES = "Website pages one workspace may learn, all its sites together; null = unlimited"
_FEATURES = "What businesses on this plan see in the dashboard; flags not sent keep their default"
_CACHED = "true: cached answers count toward the monthly question cap; false: they are free"
# PIP1 profile
_PROFILE = "The pipeline profile this plan answers with; null = the default profile"
_PROFILE_UPDATE = "A pipeline profile for this plan; null goes back to the default profile"
# /PIP1 profile
# PIP3 fair use
_FAIR_LIMITS = (
    "Fair-use limits of this plan's businesses (questions per minute / hour / day, website "
    "pages and fetches, uploads, storage, AI spend); a limit left out or null is unlimited. "
    "`website_pages_total` is `website_pages_max` and is not stored here."
)
_FAIR_POLICY = (
    "What happens when the balance runs out (`on_billing_block`) or a limit is passed "
    "(`on_fair_use_exceeded`), the downgrade profile, the hard line and the alert levels"
)
# /PIP3 fair use
# TV6 channel
_CHANNELS = "Channels this plan includes (WhatsApp, and whether Meta's fees are passed on)"
# /TV6 channel
# P5 plans
_BYOK = (
    "true: businesses on this plan may use their own AI key; false: the key stays saved but "
    "answers use the deployer's AI and the business's balance (an add-on can still unlock it)"
)
# /P5 plans
_SELF_SERVICE = (  # P6 plan choice
    "true: businesses may choose this plan themselves, from the sign-up link and their Plan & "
    "billing page; false: only the operator puts a business on it"
)


class PlanBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    max_knowledge_bases: int | None = Field(description=f"Knowledge bases; {_CAP}")
    monthly_questions: int | None = Field(
        description=f"Answered questions per UTC calendar month; {_CAP}"
    )
    max_documents: int | None = Field(description=f"Active documents; {_CAP}")
    max_widget_keys: int | None = Field(description=f"Website widget keys; {_CAP}")
    max_upload_mb: int | None = Field(
        description="Largest single upload in MB, lower than the engine's; null = the engine's"
    )
    max_document_tokens: int | None = Field(
        description="Most text one document may hold, in tokens, lower than the engine's; "
        "null = the engine's"
    )
    count_cached_answers: bool = Field(
        True,
        description="true: an answer served from the query cache counts as a question "
        "(toward the monthly cap and counter); false: cached answers are free",
    )
    is_default: bool = Field(description="New tenants get this plan")
    is_active: bool = Field(description="false: cannot be assigned; tenants on it keep it")
    is_builtin: bool = Field(
        description="The plan the engine seeded on install (Standard); read-only"
    )
    is_internal: bool = Field(
        False,
        description="The Internal plan of the operator's own workspace: unlimited, locked, "
        "assignable only to that workspace; read-only",
    )
    # BIZ3 plan
    website_learning_enabled: bool = Field(
        True, description="false: businesses on this plan cannot learn from their website"
    )
    website_pages_max: int | None = Field(
        None,
        description="Website pages one workspace may learn, all its sites together; "
        "null = unlimited (the engine's own ceiling always applies)",
    )
    dashboard_features: DashboardFeatures = Field(
        default_factory=DashboardFeatures,
        description="What businesses on this plan see in the dashboard; every flag is "
        "returned, defaults filled in",
    )

    @field_validator("dashboard_features", mode="before")
    @classmethod
    def _full_flags(cls, value: object) -> object:
        return parse_features(value if isinstance(value, dict) else None)

    # /BIZ3 plan
    # PIP1 profile
    pipeline_profile_id: UUID | None = Field(
        None,
        description="The pipeline profile this plan's businesses answer with (how questions "
        "are answered: model, re-ranker, queue priority...); null = the default profile",
    )
    pipeline_profile_label: str | None = Field(
        None, description="What owners see for it (Economy, Standard, Premium...)"
    )
    # /PIP1 profile
    # TV6 channel
    channels: ChannelsPolicy = Field(
        default_factory=ChannelsPolicy,
        description="Channels this plan includes: `whatsapp` (the WhatsApp number) and "
        "`whatsapp_fees` (`pass_through`: Meta's per-message fees are charged on to a business "
        "on the deployer's own WhatsApp account; `included`: they are not)",
    )

    @field_validator("channels", mode="before")
    @classmethod
    def _stored_channels(cls, value: object) -> object:
        return parse_channels(value)

    # /TV6 channel
    # P5 plans
    byok_allowed: bool = Field(
        True,
        description=_BYOK,
    )
    # /P5 plans
    self_service: bool = Field(False, description=_SELF_SERVICE)  # P6 plan choice
    created_at: datetime
    updated_at: datetime


class PlanOut(PlanBase):
    tenant_count: int = Field(description="Tenants on this plan")
    # PIP3 fair use: for the deployer only (a business's own plan view, `PlanBase`, never shows
    # the limits or what happens past them: owners must not see the deployer's AI spend)
    fair_use_limits: FairUseLimits = Field(default_factory=FairUseLimits, description=_FAIR_LIMITS)
    fair_use_policy: FairUsePolicy = Field(default_factory=FairUsePolicy, description=_FAIR_POLICY)

    @field_validator("fair_use_limits", mode="before")
    @classmethod
    def _stored_limits(cls, value: object) -> object:
        return parse_limits(value)

    @field_validator("fair_use_policy", mode="before")
    @classmethod
    def _stored_policy(cls, value: object) -> object:
        return parse_policy(value)

    # /PIP3 fair use


class PlanListResponse(BaseModel):
    items: list[PlanOut]
    count: int


class CreatePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    max_knowledge_bases: int | None = Field(None, ge=1, description=_CAP)
    monthly_questions: int | None = Field(None, ge=1, description=_CAP)
    max_documents: int | None = Field(None, ge=1, description=_CAP)
    max_widget_keys: int | None = Field(None, ge=1, description=_CAP)
    max_upload_mb: int | None = Field(None, ge=1, description=_ENGINE)
    max_document_tokens: int | None = Field(None, ge=1, description=_ENGINE)
    count_cached_answers: bool = Field(True, description=_CACHED)
    is_default: bool = Field(False, description="true makes it the default (the old one stops)")
    # BIZ3 plan
    website_learning_enabled: bool = Field(True, description="false: no website learning")
    website_pages_max: int | None = Field(None, ge=1, description=_PAGES)
    dashboard_features: DashboardFeatures | None = Field(None, description=_FEATURES)
    # /BIZ3 plan
    pipeline_profile_id: UUID | None = Field(None, description=_PROFILE)  # PIP1 profile
    # PIP3 fair use
    fair_use_limits: FairUseLimits | None = Field(None, description=_FAIR_LIMITS)
    fair_use_policy: FairUsePolicy | None = Field(None, description=_FAIR_POLICY)
    # /PIP3 fair use
    channels: ChannelsPolicy | None = Field(None, description=_CHANNELS)  # TV6 channel
    byok_allowed: bool = Field(True, description=_BYOK)  # P5 plans
    self_service: bool = Field(False, description=_SELF_SERVICE)  # P6 plan choice


class UpdatePlanRequest(BaseModel):
    """Only the fields sent change; a cap sent as null becomes unlimited."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    max_knowledge_bases: int | None = Field(None, ge=1, description=_CAP)
    monthly_questions: int | None = Field(None, ge=1, description=_CAP)
    max_documents: int | None = Field(None, ge=1, description=_CAP)
    max_widget_keys: int | None = Field(None, ge=1, description=_CAP)
    max_upload_mb: int | None = Field(None, ge=1, description=_ENGINE)
    max_document_tokens: int | None = Field(None, ge=1, description=_ENGINE)
    count_cached_answers: bool | None = Field(None, description=_CACHED)
    is_default: bool | None = Field(
        None, description="true makes it the default; the default cannot be set to false"
    )
    is_active: bool | None = Field(None, description="false: no longer assignable")
    # BIZ3 plan
    website_learning_enabled: bool | None = Field(None, description="false: no website learning")
    website_pages_max: int | None = Field(None, ge=1, description=_PAGES)
    dashboard_features: DashboardFeatures | None = Field(None, description=_FEATURES)
    # /BIZ3 plan
    pipeline_profile_id: UUID | None = Field(None, description=_PROFILE_UPDATE)  # PIP1 profile
    # PIP3 fair use: sent = the whole new value (null clears it)
    fair_use_limits: FairUseLimits | None = Field(None, description=_FAIR_LIMITS)
    fair_use_policy: FairUsePolicy | None = Field(None, description=_FAIR_POLICY)
    # /PIP3 fair use
    channels: ChannelsPolicy | None = Field(None, description=_CHANNELS)  # TV6 channel
    byok_allowed: bool | None = Field(None, description=_BYOK)  # P5 plans
    self_service: bool | None = Field(None, description=_SELF_SERVICE)  # P6 plan choice


class SetTenantPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID
    # TV3 notice: a move to a plan that costs the business more waits for the notice period
    # unless the business asked for it (then a reason of at least 10 characters is recorded)
    business_agreed: bool = False
    reason: str | None = Field(None, max_length=500)
    # /TV3 notice


class PlanLimits(BaseModel):
    """Effective limits (a tenant's own knowledge-base override applied); null = unlimited."""

    max_knowledge_bases: int | None
    monthly_questions: int | None
    max_documents: int | None
    max_widget_keys: int | None
    max_upload_mb: int | None
    max_document_tokens: int | None


class PlanUsage(BaseModel):
    month: str = Field(description='The UTC month counted, e.g. "2026-09"')
    questions: int = Field(description="Answered questions this month")
    knowledge_bases: int
    documents: int = Field(description="Active documents")
    widget_keys: int


class PlanUsageResponse(BaseModel):
    plan: PlanBase
    limits: PlanLimits
    usage: PlanUsage
    resets_at: datetime = Field(description="When the monthly question count starts again")
