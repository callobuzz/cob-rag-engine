"""Tenant API schemas — creation, listing, update, lifecycle, key rotation."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.core.schemas import StrictRequest

TenantStatus = Literal["pending", "active", "suspended"]


_MAX_CORPORA = "Per-tenant knowledge-base limit overriding the plan's; null follows the plan"


class CreateTenantRequest(StrictRequest):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    max_corpora: int | None = Field(None, ge=1, le=10000, description=_MAX_CORPORA)
    plan_id: UUID | None = Field(None, description="An active plan; default: the default plan")


class CreateTenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    api_key: str = Field(
        ...,
        description="Tenant API key (tk_xxx). Shown only at creation — store it securely.",
    )
    max_corpora: int | None = Field(description=_MAX_CORPORA)
    plan_id: UUID
    status: TenantStatus = "active"
    is_active: bool
    created_at: datetime


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    corpus_count: int
    max_corpora: int | None = Field(description=_MAX_CORPORA)
    effective_max_corpora: int | None = Field(
        description="The knowledge-base limit that applies (override, else plan); null = none"
    )
    plan_id: UUID
    plan_name: str
    status: TenantStatus = Field(description="pending (awaiting approval), active, suspended")
    is_active: bool = Field(description="status == active (kept for older clients)")
    is_operator: bool = Field(
        False,
        description="The operator's own workspace (the house account): on the Internal plan, "
        "never suspended, deleted only with confirm_operator_workspace. Read-only; at most "
        "one tenant has it. Not a customer: leave it out of customer counts and revenue",
    )
    has_api_key: bool = Field(
        description="The tenant has a working tk_ key (false: none was ever issued, e.g. a "
        "sign-up or the Default tenant; rotating issues one). The key itself is never shown."
    )
    activated_at: datetime | None
    created_at: datetime


class AdminTenantResponse(TenantResponse):
    activation_note: str | None
    created_by_user_id: UUID | None = Field(description="The account that signed it up")
    owner_emails: list[str]
    member_count: int
    monthly_questions: int | None = Field(description="The plan's monthly cap; null = unlimited")
    questions_this_month: int = Field(description="Answered questions this UTC month")
    # TV3 notice: set only by a plan move that waits for the notice period
    plan_change_scheduled_for: datetime | None = Field(
        None, description="When a scheduled move to a dearer plan takes effect"
    )
    # /TV3 notice
    # P6 plan choice: the self-service plan the owner chose at sign-up, while it waits for approval
    requested_plan_id: UUID | None = Field(None, description="Plan chosen at sign-up, if any")
    requested_plan_name: str | None = None
    # /P6 plan choice


class TenantListResponse(BaseModel):
    items: list[AdminTenantResponse]
    count: int = Field(description="Items on this page")
    total: int = Field(description="Tenants matching the filters")
    offset: int
    limit: int


class UpdateTenantRequest(StrictRequest):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    max_corpora: int | None = Field(
        None,
        ge=1,
        le=10000,
        description="Knowledge-base override; an explicit null removes it (follow the plan)",
    )
    is_active: bool | None = Field(
        None,
        description=(
            "false suspends an active tenant (needs confirm_name), true reactivates a "
            "suspended one; a pending tenant is activated with POST .../activate"
        ),
    )
    confirm_name: str | None = Field(
        None,
        max_length=255,
        description="The tenant's current name, typed; required with is_active: false",
    )


class ActivateTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID | None = Field(
        None, description="An active plan; default: keep the tenant's plan (the default plan)"
    )
    note: str | None = Field(
        None, max_length=1000, description='e.g. "paid 3,000 via UPI on 29 Sep"'
    )


class SuspendTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_name: str = Field(
        ..., max_length=255, description="The tenant's name, typed exactly (case-sensitive)"
    )
    reason: str | None = Field(None, max_length=1000)


class DeclineTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        None, max_length=1000, description="Kept in the audit log; not e-mailed"
    )


class RotateTenantKeyResponse(BaseModel):
    api_key: str = Field(
        ...,
        description="New tenant API key (tk_xxx). The old key is now invalid.",
    )
