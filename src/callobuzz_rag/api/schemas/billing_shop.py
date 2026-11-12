"""Bodies for the business owner's add-on shop and for service orders (P5).

The owner sees a price, what it comes to with GST, what it gives, and whether they already have
it. Never the deployer's costs or any internal note. `internal_note` exists only on the admin
view of a service order.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from callobuzz_rag.api.schemas.billing_addons import AddOnKind
from callobuzz_rag.billing.money import Currency
from callobuzz_rag.core.schemas import StrictRequest

OrderStatus = Literal["requested", "in_progress", "done", "cancelled"]
BuyWith = Literal["online", "balance"]


class ShopItem(BaseModel):
    id: uuid.UUID
    kind: AddOnKind
    kind_label: str = Field(description="`Your own AI key`, `Services` or `Credit packs`.")
    name: str
    description: str | None
    gives: str = Field(description="One line of what it gives.")
    currency: Currency
    price_minor: int = Field(description="Before GST.")
    inclusive_total_minor: int = Field(description="What it comes to with GST.")
    renews: bool
    includes_support_months: int | None
    owned: bool
    owned_until: datetime | None = None
    grant_id: uuid.UUID | None = None
    renewal_cancelled: bool = False
    buy_with: list[BuyWith]
    affordable: bool = Field(
        True,
        description="False when paying from the balance would reach the pause point.",
    )


class ShopList(BaseModel):
    items: list[ShopItem]


class BuyAddOnRequest(StrictRequest):
    payment: Literal["wallet"] = Field(
        "wallet", description="`wallet`: the price is taken from the balance."
    )
    idempotency_key: str = Field(..., pattern=r"^[A-Za-z0-9_-]{8,64}$")
    note: str | None = Field(
        None, max_length=500, description="Services: what you want, for the order."
    )

    @field_validator("note")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class GrantView(BaseModel):
    """What an owner sees of something they bought."""

    id: uuid.UUID
    add_on_id: uuid.UUID
    kind: AddOnKind
    name: str
    summary: str
    renews: bool
    starts_at: datetime
    ends_at: datetime | None
    cancelled_at: datetime | None
    active: bool


class ServiceOrderView(BaseModel):
    """A service order as its owner sees it: the status, the provider's note, the support period."""

    id: uuid.UUID
    name: str
    status: OrderStatus
    status_label: str
    owner_note: str | None
    public_note: str | None
    includes_support_months: int | None
    support_until: date | None
    support_request_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ServiceOrderList(BaseModel):
    items: list[ServiceOrderView]


class ServiceOrderAdmin(ServiceOrderView):
    tenant_id: uuid.UUID
    tenant_name: str | None
    grant_id: uuid.UUID
    internal_note: str | None


class ServiceOrderAdminList(BaseModel):
    items: list[ServiceOrderAdmin]
    total: int


class UpdateServiceOrderRequest(StrictRequest):
    """Only the fields sent change. A note sent as null clears it."""

    status: OrderStatus | None = None
    public_note: str | None = Field(None, max_length=1000, description="Shown to the owner.")
    internal_note: str | None = Field(None, max_length=4000, description="Operator only.")


class OwnKeyStatus(BaseModel):
    """`byok` on the owner's billing view: may the workspace use its own AI key, and is one saved."""

    allowed: bool
    key_saved: bool
    source: Literal["plan", "add_on"] | None
    # names of the plans that include it (empty when it is allowed or no plan does): the lock
    # text names them, because plans are the deployer's own
    plans_with_byok: list[str] = Field(default_factory=list)
