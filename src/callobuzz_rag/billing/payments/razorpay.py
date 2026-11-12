"""The Razorpay client (P1): the only code that talks to Razorpay.

Every request goes to `https://api.razorpay.com/v1` and nowhere else: `_request` takes a path (it
must start with a single `/`), never a URL, so nothing a caller or a stored value says can send a
request to another host. Redirects are not followed. The timeout is 10 s. A key, a body or a URL
with a query is never logged, and an error carries Razorpay's status and `error.code` only.

Signatures: checkout `HMAC-SHA256(key_secret, "<order_id>|<payment_id>")`, webhook
`HMAC-SHA256(webhook_secret, raw_body)`, both compared with `hmac.compare_digest` on lowercase hex.
"""

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, ClassVar, Final, Literal

import httpx

from callobuzz_rag.billing.payments.base import (
    PaymentInfo,
    PaymentProvider,
    ProviderError,
    ProviderId,
    ProviderOrder,
    RefundInfo,
    SettlementLine,
    WebhookEvent,
)

logger = logging.getLogger(__name__)

BASE_URL: Final = "https://api.razorpay.com/v1"
TIMEOUT_S: Final = 10.0
_SETTLEMENT_PAGE: Final = 100


def _moment(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=UTC) if value else None


def _notes(value: Any) -> dict[str, str]:
    """Razorpay sends `[]` for no notes and an object otherwise."""
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _int(value: Any) -> int:
    return int(value) if value is not None else 0


class RazorpayProvider(PaymentProvider):
    provider: ClassVar[ProviderId] = "razorpay"

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        webhook_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if key_id.startswith("rzp_test_"):
            self.mode: Literal["test", "live"] = "test"
        elif key_id.startswith("rzp_live_"):
            self.mode = "live"
        else:
            raise ValueError("A Razorpay key id starts with rzp_test_ or rzp_live_.")
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        self._transport = transport

    # ------------------------------------------------------------- transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/") or path.startswith("//") or "://" in path or "\\" in path:
            raise ValueError("A Razorpay request takes a path such as /orders, never a URL.")
        try:
            async with httpx.AsyncClient(
                base_url=BASE_URL,
                auth=(self._key_id, self._key_secret),
                timeout=TIMEOUT_S,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.request(method, BASE_URL + path, params=params, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError("unreachable", type(exc).__name__) from None
        return self._decode(response)

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if 200 <= status < 300:
            try:
                data = response.json()
            except ValueError:
                raise ProviderError("rejected", "unreadable answer", status=status) from None
            return data if isinstance(data, dict) else {}
        code: str | None = None
        try:
            error = response.json().get("error", {})
            code = str(error.get("code")) if error.get("code") else None
        except (ValueError, AttributeError):
            pass
        if status in (401, 403):
            raise ProviderError("auth", f"HTTP {status}", status=status, code=code)
        if status == 404:
            raise ProviderError("not_found", f"HTTP {status}", status=status, code=code)
        if status >= 500 or status == 429:
            raise ProviderError("unreachable", f"HTTP {status}", status=status, code=code)
        raise ProviderError("rejected", f"HTTP {status}", status=status, code=code)

    # ------------------------------------------------------------- signatures

    @staticmethod
    def _sign(secret: str, message: bytes) -> str:
        return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool:
        if not signature or not self._webhook_secret:
            return False
        expected = self._sign(self._webhook_secret, raw_body)
        return hmac.compare_digest(expected, signature.strip().lower())

    def verify_checkout(
        self, *, provider_order_id: str, provider_payment_id: str, signature: str
    ) -> bool:
        if not signature:
            return False
        expected = self._sign(
            self._key_secret, f"{provider_order_id}|{provider_payment_id}".encode()
        )
        return hmac.compare_digest(expected, signature.strip().lower())

    def parse_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> WebhookEvent | None:
        lowered = {k.lower(): v for k, v in headers.items()}
        event_id = (lowered.get("x-razorpay-event-id") or "").strip()
        if not event_id or len(event_id) > 64:
            return None
        try:
            data = json.loads(raw_body)
        except ValueError:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("event"), str):
            return None
        payload = data.get("payload")
        ids: dict[str, str] = {}
        if isinstance(payload, dict):
            for name in ("payment", "order", "refund", "payment_link", "dispute"):
                entity = (payload.get(name) or {}).get("entity") if payload.get(name) else None
                if isinstance(entity, dict) and isinstance(entity.get("id"), str):
                    ids[name] = entity["id"]
            payment_entity = ((payload.get("payment") or {}).get("entity")) or {}
            if "order" not in ids and isinstance(payment_entity.get("order_id"), str):
                ids["order"] = payment_entity["order_id"]
            refund_entity = ((payload.get("refund") or {}).get("entity")) or {}
            if "payment" not in ids and isinstance(refund_entity.get("payment_id"), str):
                ids["payment"] = refund_entity["payment_id"]
            dispute_entity = ((payload.get("dispute") or {}).get("entity")) or {}
            if "payment" not in ids and isinstance(dispute_entity.get("payment_id"), str):
                ids["payment"] = dispute_entity["payment_id"]
        return WebhookEvent(event_id=event_id, event=data["event"][:60], entity_ids=ids)

    # ------------------------------------------------------------- orders and payments

    async def create_order(
        self, *, amount_minor: int, currency: str, receipt: str, notes: Mapping[str, str]
    ) -> ProviderOrder:
        data = await self._request(
            "POST",
            "/orders",
            body={
                "amount": amount_minor,
                "currency": currency,
                "receipt": receipt,
                "notes": dict(notes),
            },
        )
        return ProviderOrder(
            provider_order_id=str(data["id"]),
            amount_minor=_int(data.get("amount")),
            currency=str(data.get("currency", currency)),
            receipt=str(data.get("receipt") or receipt),
        )

    def checkout_params(
        self,
        order: ProviderOrder,
        *,
        name: str,
        description: str,
        prefill: Mapping[str, str],
        notes: Mapping[str, str],
    ) -> dict[str, Any]:
        """What the page hands to Razorpay's checkout: the public key id, never a secret."""
        return {
            "key": self._key_id,
            "order_id": order.provider_order_id,
            "amount": order.amount_minor,
            "currency": order.currency,
            "name": name,
            "description": description,
            "prefill": dict(prefill),
            "notes": dict(notes),
        }

    @staticmethod
    def _payment(data: Mapping[str, Any]) -> PaymentInfo:
        status = str(data.get("status", "created"))
        if status not in ("created", "authorized", "captured", "refunded", "failed"):
            status = "failed"
        return PaymentInfo(
            provider_payment_id=str(data["id"]),
            provider_order_id=data.get("order_id"),
            status=status,  # type: ignore[arg-type]
            amount_minor=_int(data.get("amount")),
            amount_refunded_minor=_int(data.get("amount_refunded")),
            currency=str(data.get("currency", "INR")),
            method=data.get("method"),
            fee_minor=data.get("fee"),
            tax_minor=data.get("tax"),
            notes=_notes(data.get("notes")),
            captured_at=_moment(data.get("created_at")) if status == "captured" else None,
            error_code=data.get("error_code"),
        )

    async def fetch_payment(self, provider_payment_id: str) -> PaymentInfo:
        return self._payment(await self._request("GET", f"/payments/{provider_payment_id}"))

    async def payments_of_order(self, provider_order_id: str) -> list[PaymentInfo]:
        data = await self._request("GET", f"/orders/{provider_order_id}/payments")
        return [self._payment(item) for item in data.get("items", [])]

    # ------------------------------------------------------------- refunds

    @staticmethod
    def _refund(data: Mapping[str, Any]) -> RefundInfo:
        status = str(data.get("status", "pending"))
        if status not in ("pending", "processed", "failed"):
            status = "pending"
        return RefundInfo(
            provider_refund_id=str(data["id"]),
            provider_payment_id=str(data.get("payment_id", "")),
            amount_minor=_int(data.get("amount")),
            status=status,  # type: ignore[arg-type]
        )

    async def refund(
        self,
        provider_payment_id: str,
        *,
        amount_minor: int,
        receipt: str,
        notes: Mapping[str, str],
    ) -> RefundInfo:
        data = await self._request(
            "POST",
            f"/payments/{provider_payment_id}/refund",
            body={"amount": amount_minor, "receipt": receipt, "notes": dict(notes)},
        )
        return self._refund(data)

    async def fetch_refund(self, provider_payment_id: str, provider_refund_id: str) -> RefundInfo:
        return self._refund(
            await self._request(
                "GET", f"/payments/{provider_payment_id}/refunds/{provider_refund_id}"
            )
        )

    async def find_refund_by_receipt(
        self, provider_payment_id: str, receipt: str
    ) -> RefundInfo | None:
        data = await self._request(
            "GET", f"/payments/{provider_payment_id}/refunds", params={"count": 100}
        )
        for item in data.get("items", []):
            if isinstance(item, dict) and item.get("receipt") == receipt and "id" in item:
                return self._refund(item)
        return None

    # ------------------------------------------------------------- links, recon, test

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
        data = await self._request(
            "POST",
            "/payment_links",
            body={
                "amount": amount_minor,
                "currency": currency,
                "reference_id": reference_id,
                "description": description,
                "customer": dict(customer),
                "notify": {"sms": False, "email": False},
                "expire_by": int(expire_by.timestamp()),
                "notes": dict(notes),
            },
        )
        return str(data["id"]), str(data["short_url"])

    async def settlement_lines(self, day: date) -> list[SettlementLine]:
        lines: list[SettlementLine] = []
        skip = 0
        while True:
            data = await self._request(
                "GET",
                "/settlements/recon/combined",
                params={
                    "year": day.year,
                    "month": day.month,
                    "day": day.day,
                    "count": _SETTLEMENT_PAGE,
                    "skip": skip,
                },
            )
            items = data.get("items", [])
            for item in items:
                kind = str(item.get("type", "adjustment"))
                if kind not in ("payment", "refund", "adjustment", "transfer"):
                    kind = "adjustment"
                lines.append(
                    SettlementLine(
                        type=kind,  # type: ignore[arg-type]
                        entity_id=str(item.get("entity_id", "")),
                        amount_minor=_int(item.get("amount")),
                        fee_minor=_int(item.get("fee")),
                        tax_minor=_int(item.get("tax")),
                        settlement_id=item.get("settlement_id"),
                        settled_at=_moment(item.get("settled_at")),
                    )
                )
            if len(items) < _SETTLEMENT_PAGE:
                return lines
            skip += _SETTLEMENT_PAGE

    async def test_connection(self) -> None:
        await self._request("GET", "/orders", params={"count": 1})
