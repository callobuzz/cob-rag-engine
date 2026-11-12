"""An in-memory payment provider for tests (P1). The factory never builds it.

It is a `RazorpayProvider` whose network methods are replaced, so signature checking and webhook
parsing are the real code: a test signs with `sign_webhook` / `sign_checkout` and the route verifies
with the same functions it uses in production. Orders, payments and refunds live in dictionaries;
`api_calls` records every call, so a test can prove a route made none.
"""

import itertools
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

from callobuzz_rag.billing.payments.base import (
    PaymentInfo,
    ProviderError,
    ProviderOrder,
    RefundInfo,
    SettlementLine,
    WebhookEvent,
)
from callobuzz_rag.billing.payments.razorpay import RazorpayProvider

_FEE_BP = 200  # Razorpay's 2 %
_FEE_TAX_PCT = 18


@dataclass(slots=True)
class FakeWebhook:
    """A delivery as the route receives it, and the event it parses to."""

    event_id: str
    event: str
    entity_ids: Mapping[str, str]
    body: bytes
    headers: dict[str, str]

    @property
    def parsed(self) -> WebhookEvent:
        return WebhookEvent(self.event_id, self.event, dict(self.entity_ids))


@dataclass(slots=True)
class _Order:
    order: ProviderOrder
    notes: dict[str, str]
    paid: bool = False


class FakePayments(RazorpayProvider):
    """The test double. `FakePaymentProvider` is its public name."""

    def __init__(
        self,
        *,
        key_id: str = "rzp_test_fake",
        key_secret: str = "key_secret_fake",
        webhook_secret: str = "whsec_fake",
    ) -> None:
        super().__init__(key_id=key_id, key_secret=key_secret, webhook_secret=webhook_secret)
        self.api_calls: list[str] = []
        self.orders: dict[str, _Order] = {}
        self.payments: dict[str, PaymentInfo] = {}
        self.refunds: dict[str, RefundInfo] = {}
        self.links: dict[str, dict[str, Any]] = {}
        self.settlements: dict[date, list[SettlementLine]] = {}
        self.refund_status: str = "pending"
        self.fail_refund_with: ProviderError | None = None
        # the refund is made at the provider, then the answer is lost (a timeout after the fact)
        self.fail_refund_after_create = False
        self.refund_receipts: dict[tuple[str, str], str] = {}
        self.down: ProviderError | None = None
        self.bad_auth = False
        self.on_refund: Callable[[str, int], None] | None = None
        self._salt = uuid.uuid4().hex[:6]  # ids differ between fakes sharing one database
        self._counter = itertools.count(1)
        self._events = itertools.count(1)

    # ------------------------------------------------------------- helpers for tests

    def _id(self, prefix: str) -> str:
        return f"{prefix}_{self._salt}{next(self._counter):08d}"

    def _call(self, name: str) -> None:
        self.api_calls.append(name)
        if self.down is not None:
            raise self.down

    def sign_checkout(self, provider_order_id: str, provider_payment_id: str) -> str:
        return self._sign(self._key_secret, f"{provider_order_id}|{provider_payment_id}".encode())

    def sign_webhook(self, raw_body: bytes) -> str:
        return self._sign(self._webhook_secret, raw_body)

    def capture_link(self, link_id: str) -> PaymentInfo:
        """The customer paid a payment link. Razorpay makes its own order for it and the payment
        carries none of the link's notes: only the signed link id in the event names our order."""
        return self.capture(self.links[link_id]["order"], drop_notes=True)

    def capture(
        self,
        provider_order_id: str,
        *,
        method: str = "upi",
        amount_minor: int | None = None,
        drop_notes: bool = False,
    ) -> PaymentInfo:
        """The customer paid the order in full: a captured payment with Razorpay's fee."""
        held = self.orders[provider_order_id]
        amount = held.order.amount_minor if amount_minor is None else amount_minor
        fee = (amount * _FEE_BP + 5_000) // 10_000
        tax = (fee * _FEE_TAX_PCT + 50) // 100
        info = PaymentInfo(
            provider_payment_id=self._id("pay"),
            provider_order_id=provider_order_id,
            status="captured",
            amount_minor=amount,
            amount_refunded_minor=0,
            currency=held.order.currency,
            method=method,
            fee_minor=fee + tax,
            tax_minor=tax,
            notes={} if drop_notes else dict(held.notes),
            captured_at=datetime.now(UTC),
        )
        self.payments[info.provider_payment_id] = info
        held.paid = True
        return info

    def authorize(self, provider_order_id: str) -> PaymentInfo:
        info = replace(self.capture(provider_order_id), status="authorized", captured_at=None)
        self.payments[info.provider_payment_id] = info
        return info

    def fail(self, provider_order_id: str) -> PaymentInfo:
        info = replace(
            self.capture(provider_order_id),
            status="failed",
            captured_at=None,
            error_code="BAD_REQUEST_ERROR",
        )
        self.orders[provider_order_id].paid = False
        self.payments[info.provider_payment_id] = info
        return info

    def event(
        self,
        name: str,
        *,
        payment: str | None = None,
        refund: str | None = None,
        order: str | None = None,
        payment_link: str | None = None,
        dispute: str | None = None,
        amount_override: int | None = None,
        event_id: str | None = None,
    ) -> FakeWebhook:
        """A signed delivery. `amount_override` forges the amount in the payload (the worker must
        ignore it: amounts come from `fetch_payment`)."""
        payload: dict[str, Any] = {}
        ids: dict[str, str] = {}
        if payment:
            known = self.payments.get(payment)
            entity: dict[str, Any] = {"id": payment, "email": "asha@shree.example"}
            if known is not None:
                entity.update(
                    order_id=known.provider_order_id,
                    amount=known.amount_minor if amount_override is None else amount_override,
                    status=known.status,
                )
                if known.provider_order_id:
                    ids["order"] = known.provider_order_id
            payload["payment"] = {"entity": entity}
            ids["payment"] = payment
        if order:
            payload["order"] = {"entity": {"id": order}}
            ids["order"] = order
        if refund:
            payload["refund"] = {"entity": {"id": refund, "payment_id": payment}}
            ids["refund"] = refund
        if payment_link:
            payload["payment_link"] = {"entity": {"id": payment_link}}
            ids["payment_link"] = payment_link
        if dispute:
            payload["dispute"] = {"entity": {"id": dispute, "payment_id": payment}}
            ids["dispute"] = dispute
        body = json.dumps({"entity": "event", "event": name, "payload": payload}).encode()
        eid = event_id or f"evt_{self._salt}{next(self._events):08d}"
        return FakeWebhook(
            event_id=eid,
            event=name,
            entity_ids=ids,
            body=body,
            headers={
                "x-razorpay-signature": self.sign_webhook(body),
                "x-razorpay-event-id": eid,
                "content-type": "application/json",
            },
        )

    def add_settlement(self, day: date, *lines: SettlementLine) -> None:
        self.settlements.setdefault(day, []).extend(lines)

    # ------------------------------------------------------------- the provider contract

    async def create_order(
        self, *, amount_minor: int, currency: str, receipt: str, notes: Mapping[str, str]
    ) -> ProviderOrder:
        self._call("create_order")
        order = ProviderOrder(self._id("order"), amount_minor, currency, receipt)
        self.orders[order.provider_order_id] = _Order(order, dict(notes))
        return order

    async def fetch_payment(self, provider_payment_id: str) -> PaymentInfo:
        self._call("fetch_payment")
        try:
            return self.payments[provider_payment_id]
        except KeyError:
            raise ProviderError("not_found", "payment") from None

    async def payments_of_order(self, provider_order_id: str) -> list[PaymentInfo]:
        self._call("payments_of_order")
        return [p for p in self.payments.values() if p.provider_order_id == provider_order_id]

    async def refund(
        self,
        provider_payment_id: str,
        *,
        amount_minor: int,
        receipt: str,
        notes: Mapping[str, str],
    ) -> RefundInfo:
        self._call("refund")
        if self.on_refund is not None:
            self.on_refund(provider_payment_id, amount_minor)
        if self.fail_refund_with is not None and not self.fail_refund_after_create:
            raise self.fail_refund_with
        info = RefundInfo(
            self._id("rfnd"),
            provider_payment_id,
            amount_minor,
            self.refund_status,  # type: ignore[arg-type]
        )
        self.refunds[info.provider_refund_id] = info
        self.refund_receipts[(provider_payment_id, receipt)] = info.provider_refund_id
        if self.fail_refund_with is not None:
            raise self.fail_refund_with
        return info

    async def find_refund_by_receipt(
        self, provider_payment_id: str, receipt: str
    ) -> RefundInfo | None:
        self._call("find_refund")
        found = self.refund_receipts.get((provider_payment_id, receipt))
        return self.refunds[found] if found else None

    async def fetch_refund(self, provider_payment_id: str, provider_refund_id: str) -> RefundInfo:
        self._call("fetch_refund")
        try:
            return self.refunds[provider_refund_id]
        except KeyError:
            raise ProviderError("not_found", "refund") from None

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
    ) -> tuple[str, str]:
        self._call("create_payment_link")
        link_id = self._id("plink")
        order = ProviderOrder(self._id("order"), amount_minor, currency, reference_id)
        self.orders[order.provider_order_id] = _Order(order, dict(notes))
        self.links[link_id] = {
            "order": order.provider_order_id,
            "reference_id": reference_id,
            "expire_by": expire_by,
        }
        return link_id, f"https://rzp.io/i/{link_id[-8:]}"

    async def settlement_lines(self, day: date) -> list[SettlementLine]:
        self._call("settlement_lines")
        return list(self.settlements.get(day, []))

    async def test_connection(self) -> None:
        self._call("test_connection")
        if self.bad_auth:
            raise ProviderError("auth", "HTTP 401", status=401)


FakePaymentProvider = FakePayments
