"""The Razorpay client, through httpx.MockTransport only (P1 T4). Nothing here reaches the network."""

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
import pytest

from callobuzz_rag.billing.payments.base import ProviderError
from callobuzz_rag.billing.payments.razorpay import RazorpayProvider

WEBHOOK_BODY = b'{"entity":"event","event":"payment.captured"}'
KEY_SECRET = "key_secret_test"
WEBHOOK_SECRET = "whsec_test_secret"
WEBHOOK_SIG = hmac.new(WEBHOOK_SECRET.encode(), WEBHOOK_BODY, hashlib.sha256).hexdigest()
CHECKOUT_SIG = hmac.new(
    KEY_SECRET.encode(), b"order_TEST123|pay_TEST456", hashlib.sha256
).hexdigest()


def provider(handler: Callable[[httpx.Request], httpx.Response]) -> RazorpayProvider:
    return RazorpayProvider(
        key_id="rzp_test_abc123",
        key_secret=KEY_SECRET,
        webhook_secret=WEBHOOK_SECRET,
        transport=httpx.MockTransport(handler),
    )


def test_signature_vectors() -> None:
    p = provider(lambda r: httpx.Response(500))
    assert p.verify_webhook(WEBHOOK_BODY, WEBHOOK_SIG)
    assert p.verify_webhook(WEBHOOK_BODY, WEBHOOK_SIG.upper())  # hex case does not matter
    assert not p.verify_webhook(WEBHOOK_BODY + b" ", WEBHOOK_SIG)  # raw bytes, never re-serialised
    assert not p.verify_webhook(WEBHOOK_BODY, None)
    assert not p.verify_webhook(WEBHOOK_BODY, "")
    assert p.verify_checkout(
        provider_order_id="order_TEST123", provider_payment_id="pay_TEST456", signature=CHECKOUT_SIG
    )
    assert not p.verify_checkout(
        provider_order_id="order_TEST123", provider_payment_id="pay_OTHER", signature=CHECKOUT_SIG
    )


def test_a_known_sha256_vector() -> None:
    """HMAC-SHA256 of the documented example (RFC 4231 test case 2), so the algorithm is pinned."""
    p = RazorpayProvider(key_id="rzp_test_x", key_secret="k", webhook_secret="Jefe")
    body = b"what do ya want for nothing?"
    assert p.verify_webhook(
        body, "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"
    )


def test_mode_from_key_prefix() -> None:
    assert provider(lambda r: httpx.Response(200)).mode == "test"
    assert RazorpayProvider(key_id="rzp_live_x", key_secret="s", webhook_secret="w").mode == "live"
    with pytest.raises(ValueError):
        RazorpayProvider(key_id="pk_live_x", key_secret="s", webhook_secret="w")


async def test_only_talks_to_api_razorpay_com_over_https() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(
            200, json={"id": "order_1", "amount": 118000, "currency": "INR", "receipt": "r1"}
        )

    p = provider(handler)
    await p.create_order(amount_minor=118000, currency="INR", receipt="r1", notes={})
    assert [(u.scheme, u.host, u.path) for u in seen] == [
        ("https", "api.razorpay.com", "/v1/orders")
    ]
    for bad in ("https://evil.example/v1/orders", "//evil.example/v1/orders", "orders", "/\\x"):
        with pytest.raises(ValueError):
            await p._request("GET", bad)
    assert len(seen) == 1  # nothing was sent for the refused paths


async def test_redirects_are_not_followed() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    with pytest.raises(ProviderError):
        await provider(handler).test_connection()
    assert hosts == ["api.razorpay.com"]


async def test_errors_carry_no_secret() -> None:
    p = provider(
        lambda r: httpx.Response(
            401,
            json={"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}},
        )
    )
    with pytest.raises(ProviderError) as err:
        await p.test_connection()
    assert err.value.kind == "auth" and err.value.code == "BAD_REQUEST_ERROR"
    assert KEY_SECRET not in str(err.value) and "rzp_test_abc123" not in repr(err.value)


@pytest.mark.parametrize(
    ("status", "kind"),
    [(400, "rejected"), (404, "not_found"), (500, "unreachable"), (429, "unreachable")],
)
async def test_status_maps_to_a_kind(status: int, kind: str) -> None:
    with pytest.raises(ProviderError) as err:
        await provider(lambda r: httpx.Response(status, json={})).fetch_payment("pay_1")
    assert err.value.kind == kind


async def test_unreachable_is_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom with rzp_test_abc123 inside")

    with pytest.raises(ProviderError) as err:
        await provider(handler).test_connection()
    assert err.value.kind == "unreachable" and "rzp_test" not in str(err.value)


async def test_fetch_payment_maps_fee_and_notes() -> None:
    body = {
        "id": "pay_9",
        "order_id": "order_9",
        "status": "captured",
        "amount": 118000,
        "amount_refunded": 0,
        "currency": "INR",
        "method": "upi",
        "fee": 2832,
        "tax": 432,
        "notes": {"cob_order": "abc", "cob_account": "def"},
        "created_at": 1_790_000_000,
    }
    info = await provider(lambda r: httpx.Response(200, json=body)).fetch_payment("pay_9")
    assert (info.status, info.amount_minor, info.fee_minor, info.tax_minor) == (
        "captured",
        118000,
        2832,
        432,
    )
    assert info.notes == {"cob_order": "abc", "cob_account": "def"}
    assert info.captured_at == datetime.fromtimestamp(1_790_000_000, tz=UTC)


async def test_empty_notes_arrive_as_a_list() -> None:
    body = {"id": "pay_9", "status": "created", "amount": 100, "notes": [], "currency": "INR"}
    info = await provider(lambda r: httpx.Response(200, json=body)).fetch_payment("pay_9")
    assert info.notes == {} and info.captured_at is None


def test_parse_webhook_needs_event_id_header() -> None:
    p = provider(lambda r: httpx.Response(200))
    body = (
        b'{"event":"refund.processed","payload":{"refund":{"entity":{"id":"rfnd_1",'
        b'"payment_id":"pay_1"}}}}'
    )
    assert p.parse_webhook(body, {}) is None
    event = p.parse_webhook(body, {"X-Razorpay-Event-Id": "evt_1"})
    assert event is not None and event.event == "refund.processed"
    assert dict(event.entity_ids) == {"refund": "rfnd_1", "payment": "pay_1"}
    assert p.parse_webhook(b"not json", {"x-razorpay-event-id": "evt_1"}) is None
    assert p.parse_webhook(body, {"x-razorpay-event-id": "e" * 65}) is None


async def test_refund_and_link_and_settlements_use_the_documented_paths() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/refund"):
            return httpx.Response(
                200,
                json={"id": "rfnd_1", "payment_id": "pay_1", "amount": 5000, "status": "pending"},
            )
        if request.url.path.endswith("/payment_links"):
            return httpx.Response(200, json={"id": "plink_1", "short_url": "https://rzp.io/i/x"})
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "entity_id": "pay_1",
                        "type": "payment",
                        "amount": 118000,
                        "fee": 2360,
                        "tax": 360,
                        "settlement_id": "setl_1",
                        "settled_at": 1_790_000_000,
                    }
                ]
            },
        )

    p = provider(handler)
    refund = await p.refund("pay_1", amount_minor=5000, receipt="r", notes={})
    link = await p.create_payment_link(
        amount_minor=118000,
        currency="INR",
        reference_id="o1",
        description="d",
        customer={"email": "a@b.in"},
        expire_by=datetime(2026, 10, 15, tzinfo=UTC),
        notes={},
    )
    lines = await p.settlement_lines(date(2026, 10, 7))
    assert refund.status == "pending" and link == ("plink_1", "https://rzp.io/i/x")
    assert lines[0].fee_minor == 2360 and lines[0].settlement_id == "setl_1"
    assert seen == [
        ("POST", "/v1/payments/pay_1/refund"),
        ("POST", "/v1/payment_links"),
        ("GET", "/v1/settlements/recon/combined"),
    ]


def test_checkout_params_hold_the_public_key_and_no_secret() -> None:
    from callobuzz_rag.billing.payments.base import ProviderOrder

    p = provider(lambda r: httpx.Response(200))
    params = p.checkout_params(
        ProviderOrder("order_1", 118000, "INR", "r1"),
        name="Sahayak",
        description="Add money",
        prefill={"email": "asha@shree.example"},
        notes={"cob_order": "x"},
    )
    assert params["key"] == "rzp_test_abc123" and params["order_id"] == "order_1"
    assert KEY_SECRET not in repr(params) and WEBHOOK_SECRET not in repr(params)
