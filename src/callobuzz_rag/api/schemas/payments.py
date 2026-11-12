"""Bodies for online payments and tax details (P1).

Money is an integer in minor units (paise) next to a `currency`, never a float. Owner-facing
text says balance, top-up, plan, add-on, payment, refund; never ledger, order or webhook.
"""

import re
import uuid
from datetime import date, datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from callobuzz_rag.accounts.email_rules import ValidatedEmail
from callobuzz_rag.core.company import STATES
from callobuzz_rag.core.schemas import StrictRequest

# Names and addresses on a tax invoice are Latin script (the GST portal's practice; the invoice
# PDF cannot shape other scripts).
_LATIN: Final = re.compile(r"^[\x20-\x7EÀ-ɏ]+$")
LATIN_HELP: Final = "Please type this in English letters (A-Z), as it appears on your GST records."


def _latin(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value and not _LATIN.match(value):
        raise ValueError(LATIN_HELP)
    return value or None


class TaxDetailsRequest(StrictRequest):
    """Who the tax invoice is made out to. A GST number is optional; with one, its state wins."""

    legal_name: str = Field(..., min_length=1, max_length=200)
    gstin: str | None = Field(None, max_length=40)
    address_line1: str = Field(..., min_length=1, max_length=200)
    address_line2: str | None = Field(None, max_length=200)
    city: str = Field(..., min_length=1, max_length=100)
    state_code: str | None = Field(None, max_length=2)
    pincode: str | None = Field(None, pattern=r"^[1-9][0-9]{5}$")
    country: str = Field("IN", pattern=r"^[A-Z]{2}$")
    email_for_invoices: ValidatedEmail | None = None

    @field_validator("legal_name", "address_line1", "city")
    @classmethod
    def _required_latin(cls, value: str) -> str:
        cleaned = _latin(value)
        if cleaned is None:
            raise ValueError("This is needed.")
        return cleaned

    @field_validator("address_line2")
    @classmethod
    def _optional_latin(cls, value: str | None) -> str | None:
        return _latin(value)

    @field_validator("gstin")
    @classmethod
    def _blank_gstin(cls, value: str | None) -> str | None:
        return value.strip().upper() or None if value is not None else None

    @field_validator("state_code")
    @classmethod
    def _state(cls, value: str | None) -> str | None:
        if value is not None and value not in STATES:
            raise ValueError("Please choose a state from the list.")
        return value

    @model_validator(mode="after")
    def _india_needs_a_state(self) -> "TaxDetailsRequest":
        if self.country == "IN" and self.state_code is None and self.gstin is None:
            raise ValueError("Please choose your state.")
        return self


class TaxDetailsOut(BaseModel):
    """A workspace's tax details; empty strings and nulls while none are saved."""

    legal_name: str = ""
    gstin: str | None = None
    address_line1: str = ""
    address_line2: str | None = None
    city: str = ""
    state_code: str | None = None
    state_name: str | None = None
    pincode: str | None = None
    country: str = "IN"
    email_for_invoices: str | None = None
    complete: bool = Field(
        False, description="True once the name, address and state needed for an invoice are saved."
    )


class SellerTaxProfileOut(BaseModel):
    """The deployer's GST details, with an example of the next invoice number."""

    gstin: str | None
    sac_default: str
    invoice_prefix: str
    credit_note_prefix: str
    lut_number: str | None
    lut_valid_until: date | None
    prices_include_gst: bool
    footer_text: str | None
    signature_line: str
    number_example: str


# ------------------------------------------------------------------ operator payment settings


class PaymentSettingsRequest(StrictRequest):
    """Save the payment provider and its keys. For each secret: omit (or null) to keep what is
    saved, send an empty string to remove it, send a value to replace it."""

    provider: Literal["none", "razorpay"]
    key_id: str | None = Field(None, max_length=64)
    key_secret: SecretStr | None = Field(None, max_length=200)
    webhook_secret: SecretStr | None = Field(None, max_length=200)
    min_top_up_minor: int | None = Field(None, ge=100, le=10**9)
    max_payment_minor: int | None = Field(None, ge=100, le=10**10)
    link_expiry_days: int | None = Field(None, ge=1, le=30)


class PaymentSettingsOut(BaseModel):
    """What is in force. Secrets are never returned, only whether each is saved."""

    provider: Literal["none", "razorpay"]
    key_id: str | None
    mode: Literal["test", "live"] | None
    key_secret_set: bool
    webhook_secret_set: bool
    source: Literal["env", "stored"] = Field(
        ..., description="env: the engine's environment decides; stored: saved on this page."
    )
    enabled: bool = Field(..., description="Online payments are switched on and usable.")
    webhook_url: str
    events: list[str]
    min_top_up_minor: int
    max_payment_minor: int
    link_expiry_days: int


class PaymentTestOut(BaseModel):
    ok: bool
    mode: Literal["test", "live"]


# ------------------------------------------------------------------ paying: quote and order

PurposeName = Literal["top_up", "plan", "add_on"]


class PaymentConfigOut(BaseModel):
    """What the Pay page needs before it asks for anything."""

    enabled: bool
    mode: Literal["test", "live"] | None
    key_id: str | None
    min_top_up_minor: int
    max_payment_minor: int
    currency: str
    tax_details_complete: bool
    prices_include_gst: bool


class OrderQuoteRequest(StrictRequest):
    purpose: PurposeName
    amount_minor: int | None = Field(
        None,
        ge=1,
        le=10**10,
        description="Top-ups: the amount for the balance (the total when prices include GST).",
    )
    add_on_id: uuid.UUID | None = None


class CreateOrderRequest(OrderQuoteRequest):
    idempotency_key: str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")


class OrderQuoteOut(BaseModel):
    """What the owner will pay, before and after GST. The balance receives `taxable_minor`
    (or the pack's credit for a credit pack)."""

    purpose: PurposeName
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    tax_minor: int
    gross_minor: int
    currency: Literal["INR"]
    description: str
    tax_kind: Literal["intra", "inter", "export_lut", "export_igst", "no_gst"]
    place_of_supply: str
    # P2 invoices: what the payment will produce
    document_type: Literal["invoice", "bill_of_supply"] = "invoice"
    # /P2 invoices


class OrderOut(BaseModel):
    """An order ready to pay: the quote and the parameters for the payment window."""

    order_id: uuid.UUID
    status: Literal["created", "paid", "failed", "expired"]
    mode: Literal["test", "live"]
    quote: OrderQuoteOut
    checkout: dict[str, Any]


class VerifyPaymentRequest(StrictRequest):
    """What the payment window hands back when the customer has paid."""

    provider_payment_id: str = Field(..., min_length=3, max_length=40)
    signature: str = Field(..., min_length=8, max_length=200)


class VerifyPaymentOut(BaseModel):
    status: Literal["captured", "pending"] = Field(
        ...,
        description="captured: the money is in your balance. pending: we are still confirming.",
    )
    available_minor: int | None = Field(
        None, description="The balance after the payment, once it is captured."
    )


# ------------------------------------------------------------------ the operator's payment views

PaymentStatusName = Literal[
    "captured", "authorized", "failed", "mismatch", "refunded", "partially_refunded"
]
RefundStatusName = Literal["requested", "pending", "processed", "failed"]


class PaymentRow(BaseModel):
    """One payment in the operator's list. Amounts are minor units (paise)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    provider_payment_id: str
    purpose: PurposeName
    description: str
    method: str | None
    status: PaymentStatusName
    currency: str
    gross_minor: int
    taxable_minor: int
    tax_minor: int
    refunded_minor: int
    fee_minor: int | None
    fee_tax_minor: int | None
    captured_at: datetime | None
    settled_at: datetime | None
    created_at: datetime


class PaymentListResponse(BaseModel):
    items: list[PaymentRow]
    total: int


class RefundOut(BaseModel):
    id: uuid.UUID
    payment_id: uuid.UUID
    status: RefundStatusName
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    gross_minor: int
    provider_refund_id: str | None
    memo: str
    requested_by: str
    created_at: datetime
    processed_at: datetime | None
    # P2 invoices
    credit_note_skipped: Literal["deadline", "no_invoice"] | None = Field(
        None, description="Why this refund got no credit note, when it did not."
    )
    # /P2 invoices


class PaymentEventRow(BaseModel):
    """A stored webhook delivery, without its payload."""

    id: uuid.UUID
    event_id: str
    event: str
    status: Literal["pending", "processing", "done", "ignored", "failed"]
    attempts: int
    error: str | None
    received_at: datetime
    processed_at: datetime | None


class PaymentEventListResponse(BaseModel):
    items: list[PaymentEventRow]


# P2 invoices
class CreditNoteInfo(BaseModel):
    """Whether a refund of this payment can still get a credit note (section 34 deadline)."""

    possible: bool
    deadline: date | None = Field(
        None, description="30 November after the financial year of the invoice."
    )
    invoice_number: str | None = None


# /P2 invoices


class PaymentDetail(PaymentRow):
    order_id: uuid.UUID
    order_channel: Literal["checkout", "link"]
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    tax_kind: str
    place_of_supply: str
    provider_order_id: str | None
    settlement_id: str | None
    events: list[PaymentEventRow]
    refunds: list[RefundOut]
    refundable_minor: int = Field(
        ..., description="What the operator can still refund from this payment, from the balance."
    )
    refund_deadline: date | None = Field(
        None, description="The last day the payment service accepts a refund of this payment."
    )
    credit_note: CreditNoteInfo | None = None  # P2 invoices


class RefundRequest(StrictRequest):
    """Refund part of a payment from the workspace's unused balance. `amount_minor` is what is
    taken off the balance (the amount before GST); the customer gets that plus its GST back."""

    amount_minor: int = Field(..., ge=1, le=10**10)
    memo: str = Field(..., min_length=3, max_length=500)
    confirm_amount_minor: int | None = Field(
        None, description="Type the amount again for a large refund."
    )
    idempotency_key: str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")


class PaymentLinkRequest(StrictRequest):
    purpose: PurposeName
    amount_minor: int | None = Field(None, ge=1, le=10**10)
    add_on_id: uuid.UUID | None = None
    send_email: bool = Field(True, description="E-mail the link to the workspace's owners.")


class PaymentLinkOut(BaseModel):
    order_id: uuid.UUID
    short_url: str
    expires_at: datetime
    gross_minor: int
    emailed: bool


class PaymentView(BaseModel):
    """A payment as its workspace sees it: no fees, no provider detail."""

    id: uuid.UUID
    date: datetime
    description: str
    status: PaymentStatusName
    currency: str
    gross_minor: int
    taxable_minor: int
    tax_minor: int
    refunded_minor: int
    method: str | None
    reference: str = Field(..., description="The last characters of the payment id, to quote.")


class PaymentViewList(BaseModel):
    items: list[PaymentView]
    total: int


class RetryOut(BaseModel):
    queued: bool
