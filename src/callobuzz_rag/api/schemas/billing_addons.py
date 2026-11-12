"""Bodies for the add-on catalogue and grants routes (BIL5).

Money is an integer in minor units next to a `currency`. An add-on has exactly the fields of its
kind: a credit pack a credit (and optionally a bonus and an expiry), an extra limit one limit field
with an amount and a duration, a question pack a number of questions for the month.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from callobuzz_rag.billing.money import Currency
from callobuzz_rag.core.schemas import StrictRequest

AddOnKind = Literal[
    "credit_pack", "extra_limit", "question_pack", "feature_unlock", "service"
]  # P5 plans
Feature = Literal["byok"]  # P5 plans
LimitField = Literal[
    "max_knowledge_bases",
    "max_documents",
    "max_widget_keys",
    "max_upload_mb",
    "max_document_tokens",
]
Duration = Literal["period", "permanent"]
Payment = Literal["wallet", "external", "free"]
GrantPayment = Literal["wallet", "external", "free", "gateway"]  # P5 plans: paid online (P1)
SAC_PATTERN = r"^99[0-9]{4}$"  # P5 plans

_KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "credit_pack": ("credit_minor", "bonus_pct", "expiry_days"),
    "extra_limit": ("limit_field", "limit_delta", "duration"),
    "question_pack": ("questions",),
    "feature_unlock": ("feature", "duration", "renews"),  # P5 plans
    "service": ("includes_support_months", "renews"),  # P5 plans
}
_REQUIRED: dict[str, tuple[str, ...]] = {
    "credit_pack": ("credit_minor",),
    "extra_limit": ("limit_field", "limit_delta", "duration"),
    "question_pack": ("questions",),
    "feature_unlock": ("feature", "duration"),  # P5 plans
    "service": (),  # P5 plans
}
_ALL_KIND_FIELDS = (
    "credit_minor",
    "bonus_pct",
    "expiry_days",
    "limit_field",
    "limit_delta",
    "duration",
    "questions",
    "feature",  # P5 plans
    "renews",  # P5 plans
    "includes_support_months",  # P5 plans
)
_LABELS = {
    "credit_minor": "credit",
    "limit_field": "limit",
    "limit_delta": "amount",
    "duration": "duration",
    "questions": "number of questions",
    "feature": "feature",  # P5 plans
}


def check_fields_match_kind(kind: str, values: dict[str, object]) -> None:
    """Raises `ValueError` unless exactly the fields of `kind` are set (a bonus of 0 counts as
    not set)."""
    for field in _REQUIRED[kind]:
        if values.get(field) is None:
            raise ValueError(f"A {kind.replace('_', ' ')} needs its {_LABELS[field]}.")
    for field in _ALL_KIND_FIELDS:
        if field in _KIND_FIELDS[kind]:
            continue
        value = values.get(field)
        if value is not None and value != 0:
            raise ValueError(f"A {kind.replace('_', ' ')} has no {field}.")
    # P5 plans: only a monthly own-key unlock or a service can renew
    if values.get("renews") and not (
        kind == "service" or (kind == "feature_unlock" and values.get("duration") == "period")
    ):
        raise ValueError("Only a monthly add-on that unlocks a feature, or a service, can renew.")
    # /P5 plans


class CreateAddOnRequest(StrictRequest):
    kind: AddOnKind
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=1000)
    currency: Currency
    price_minor: int = Field(..., ge=0, le=10**12)
    credit_minor: int | None = Field(
        None, ge=1, le=10**12, description="Credit packs: the credit the customer receives."
    )
    bonus_pct: int = Field(0, ge=0, le=100, description="Credit packs: bonus on top, in percent.")
    expiry_days: int | None = Field(
        None, ge=1, le=3650, description="Credit packs: days until the credit expires."
    )
    limit_field: LimitField | None = Field(None, description="Extra limits: what grows.")
    limit_delta: int | None = Field(
        None, ge=1, le=1_000_000, description="Extra limits: by how much."
    )
    questions: int | None = Field(
        None, ge=1, le=10_000_000, description="Question packs: extra questions this month."
    )
    duration: Duration | None = Field(
        None,
        description=(
            "Extra limits and feature unlocks: this month (`period`) or until revoked "
            "(`permanent`)."
        ),
    )
    # P5 plans
    feature: Feature | None = Field(None, description="Feature unlocks: what it unlocks.")
    renews: bool = Field(
        False,
        description=(
            "Monthly feature unlocks and services: charged again from the balance each period."
        ),
    )
    includes_support_months: int | None = Field(
        None, ge=1, le=36, description="Services: months of support included after it is done."
    )
    sac_code: str | None = Field(
        None, pattern=SAC_PATTERN, description="The SAC (service code) shown on the tax invoice."
    )
    sold_to_owners: bool = Field(
        False, description="Show it in the business owner's shop (Plan & billing)."
    )
    # /P5 plans

    @model_validator(mode="after")
    def _fields_match_kind(self) -> "CreateAddOnRequest":
        check_fields_match_kind(self.kind, self.model_dump())
        return self

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("The name cannot be empty.")
        return value


class UpdateAddOnRequest(StrictRequest):
    """Any field except the kind; only the fields sent change. The result must still fit the
    kind. Grants already made keep the values they were made with."""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=1000)
    currency: Currency | None = None
    price_minor: int | None = Field(None, ge=0, le=10**12)
    credit_minor: int | None = Field(None, ge=1, le=10**12)
    bonus_pct: int | None = Field(None, ge=0, le=100)
    expiry_days: int | None = Field(None, ge=1, le=3650)
    limit_field: LimitField | None = None
    limit_delta: int | None = Field(None, ge=1, le=1_000_000)
    questions: int | None = Field(None, ge=1, le=10_000_000)
    duration: Duration | None = None
    is_active: bool | None = None
    # P5 plans
    feature: Feature | None = None
    renews: bool | None = None
    includes_support_months: int | None = Field(None, ge=1, le=36)
    sac_code: str | None = Field(None, pattern=SAC_PATTERN)
    sold_to_owners: bool | None = None
    # `false` = "keep this price": the seeded price is real, so the add-on may go on sale
    placeholder: Literal[False] | None = None
    # /P5 plans

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("The name cannot be empty.")
        return value


class AddOnOut(BaseModel):
    id: uuid.UUID
    kind: AddOnKind
    name: str
    description: str | None
    currency: Currency
    price_minor: int
    credit_minor: int | None
    bonus_pct: int
    expiry_days: int | None
    limit_field: LimitField | None
    limit_delta: int | None
    questions: int | None
    duration: Duration | None
    # P5 plans
    feature: Feature | None
    renews: bool
    includes_support_months: int | None
    sac_code: str | None
    sold_to_owners: bool
    placeholder: bool  # a seeded price the deployer has not set yet
    # /P5 plans
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AddOnListResponse(BaseModel):
    items: list[AddOnOut]


class ApplyAddOnRequest(StrictRequest):
    add_on_id: uuid.UUID
    payment: Payment = Field(
        "external",
        description=(
            "`external`: paid outside the engine (the default). `free`: a gift. `wallet`: the "
            "price is taken from the customer's balance (extra limits and question packs only)."
        ),
    )
    memo: str | None = Field(None, max_length=500)
    idempotency_key: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_-]{8,64}$",
        description="One per dialog open: sending the same key again changes nothing.",
    )


class RevokeGrantRequest(StrictRequest):
    memo: str | None = Field(None, max_length=500)


class GrantOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    add_on_id: uuid.UUID
    kind: AddOnKind
    name: str
    currency: Currency
    price_minor: int
    credit_minor: int | None
    bonus_pct: int
    limit_field: LimitField | None
    limit_delta: int | None
    questions: int | None
    # P5 plans
    feature: Feature | None
    renews: bool
    includes_support_months: int | None
    sac_code: str | None
    cancelled_at: datetime | None
    renewed_through: date | None
    # /P5 plans
    payment: GrantPayment  # P5 plans: includes `gateway` (paid online)
    entry_id: uuid.UUID | None
    starts_at: datetime
    ends_at: datetime | None
    revoked_at: datetime | None
    revoked_by: str | None
    revoke_memo: str | None
    granted_by: str
    memo: str | None
    created_at: datetime
    active: bool
    summary: str


class GrantListResponse(BaseModel):
    items: list[GrantOut]


class ActiveAddOn(BaseModel):
    """What the workspace owner sees of an add-on in force: no price, no payment."""

    name: str
    kind: AddOnKind
    summary: str
    ends_at: datetime | None
