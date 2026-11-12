"""The payment provider contract (P1): an ABC and the small value types it speaks.

Razorpay is the first implementation (`razorpay.py`); `fake.py` is the in-memory one the tests use.
Amounts are integers in minor units (paise), never floats. Nothing here holds a key in a message.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Literal

from callobuzz_rag.core.exceptions import RAGKernelError

ProviderId = Literal["razorpay"]
Purpose = Literal["top_up", "plan", "add_on"]
OrderStatus = Literal["created", "paid", "failed", "expired"]
PaymentStatus = Literal["authorized", "captured", "failed", "refunded", "partially_refunded"]
ProviderPaymentState = Literal["created", "authorized", "captured", "refunded", "failed"]


class ProviderError(RAGKernelError):
    """A provider call failed. `kind` is auth, unreachable, rejected or not_found; the message
    never carries a key, a body or a URL with a query."""

    def __init__(
        self,
        kind: Literal["auth", "unreachable", "rejected", "not_found"],
        message: str = "",
        *,
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        text = message or kind
        super().__init__(f"{kind}: {text}")
        self.kind = kind
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class ProviderOrder:
    provider_order_id: str
    amount_minor: int
    currency: str
    receipt: str


@dataclass(frozen=True, slots=True)
class PaymentInfo:
    """The provider's truth for one payment (what `fetch_payment` returns)."""

    provider_payment_id: str
    provider_order_id: str | None
    status: ProviderPaymentState
    amount_minor: int
    amount_refunded_minor: int
    currency: str
    method: str | None
    fee_minor: int | None
    tax_minor: int | None
    notes: Mapping[str, str] = field(default_factory=dict)
    captured_at: datetime | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class RefundInfo:
    provider_refund_id: str
    provider_payment_id: str
    amount_minor: int
    status: Literal["pending", "processed", "failed"]


@dataclass(frozen=True, slots=True)
class SettlementLine:
    type: Literal["payment", "refund", "adjustment", "transfer"]
    entity_id: str
    amount_minor: int
    fee_minor: int
    tax_minor: int
    settlement_id: str | None
    settled_at: datetime | None


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """A webhook after its signature passed: which event, and which entities it names."""

    event_id: str
    event: str
    entity_ids: Mapping[str, str]


class PaymentProvider(ABC):
    provider: ClassVar[ProviderId]
    mode: Literal["test", "live"]

    @abstractmethod
    async def create_order(
        self, *, amount_minor: int, currency: str, receipt: str, notes: Mapping[str, str]
    ) -> ProviderOrder: ...

    @abstractmethod
    def checkout_params(
        self,
        order: ProviderOrder,
        *,
        name: str,
        description: str,
        prefill: Mapping[str, str],
        notes: Mapping[str, str],
    ) -> dict[str, Any]: ...

    @abstractmethod
    def verify_checkout(
        self, *, provider_order_id: str, provider_payment_id: str, signature: str
    ) -> bool: ...

    @abstractmethod
    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool: ...

    @abstractmethod
    def parse_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> WebhookEvent | None: ...

    @abstractmethod
    async def fetch_payment(self, provider_payment_id: str) -> PaymentInfo: ...

    @abstractmethod
    async def payments_of_order(self, provider_order_id: str) -> list[PaymentInfo]: ...

    @abstractmethod
    async def refund(
        self,
        provider_payment_id: str,
        *,
        amount_minor: int,
        receipt: str,
        notes: Mapping[str, str],
    ) -> RefundInfo: ...

    @abstractmethod
    async def fetch_refund(
        self, provider_payment_id: str, provider_refund_id: str
    ) -> RefundInfo: ...

    @abstractmethod
    async def find_refund_by_receipt(
        self, provider_payment_id: str, receipt: str
    ) -> RefundInfo | None:
        """The refund of this payment that was sent with `receipt`, or None. A resend looks here
        first, so a refund whose answer we never saw is never paid twice."""

    @abstractmethod
    async def create_payment_link(
        self,
        *,
        amount_minor: int,
        currency: str,
        reference_id: str,
        description: str,
        customer: Mapping[str, str],
        expire_by: datetime,
        notes: Mapping[str, str],
    ) -> tuple[str, str]: ...

    @abstractmethod
    async def settlement_lines(self, day: date) -> list[SettlementLine]: ...

    @abstractmethod
    async def test_connection(self) -> None: ...
