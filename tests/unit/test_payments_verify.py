"""The owner's checkout confirmation (`POST .../orders/{id}/verify`), P1 T6.

Through the real app on the in-memory database; the ledger is the recording stand-in. The SQL of
the capture itself (locks, races, rollback) is proved on PostgreSQL in
`tests/integration/test_payments_capture_db.py`.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.settings_store import SETTINGS_ROW_ID, invalidate_engine_settings_cache
from callobuzz_rag.billing import ledger
from callobuzz_rag.billing.payments.base import ProviderError
from callobuzz_rag.billing.payments.fake import FakePaymentProvider
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.payment import BillingProfile, Payment, PaymentOrder
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import World, auth_app, login, req
from tests.unit.test_billing_ledger_api import fake_append

ORDERS = "/api/v1/billing/payments/orders"


@pytest.fixture
def world() -> World:
    invalidate_engine_settings_cache()
    w = World()
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    w.member(w.user("meena@pune.example"), w.pune, "owner")
    w.db.add(stored_pricing(w.db.default_plan, subscription_fee_minor=0))
    w.db.add(
        EngineSettingsRow(
            id=SETTINGS_ROW_ID,
            value={
                "company": {"legal_name": "Callobuzz", "state_code": "27"},
                "invoicing": {"gstin": "27AAPFU0939F1ZV"},
            },
        )
    )
    w.db.add(
        BillingProfile(
            tenant_id=w.shree.id,
            legal_name="Sharma Furniture",
            address_line1="Paud Road",
            city="Pune",
            state_code="27",
            country="IN",
        )
    )
    return w


@contextmanager
def app_for(world: World, fake: FakePaymentProvider) -> Iterator[Any]:
    with (
        auth_app(world) as app,
        fake_accounts(world.db),
        patch.object(ledger, "append_entries", fake_append(world)),
        patch.object(ledger, "available_minor", AsyncMock(return_value=100_000)),
        patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()),
    ):
        app.state.payment_provider_override = fake
        yield app


async def call(app: Any, url: str, body: dict[str, Any], email: str = "asha@shree.example") -> Any:
    token = (await login(app, email)).json()["access_token"]
    return await req(app, "POST", url, token=token, json=body)


async def order_for(app: Any, fake: FakePaymentProvider) -> tuple[str, str]:
    res = await call(
        app,
        ORDERS,
        {"purpose": "top_up", "amount_minor": 100_000, "idempotency_key": "order-key-0001"},
    )
    assert res.status_code == 201, res.text
    return res.json()["order_id"], res.json()["checkout"]["order_id"]


def verify_body(fake: FakePaymentProvider, provider_order: str, pay_id: str) -> dict[str, str]:
    return {
        "provider_payment_id": pay_id,
        "signature": fake.sign_checkout(provider_order, pay_id),
    }


async def test_a_signed_captured_payment_reaches_the_balance(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        order_id, provider_order = await order_for(app, fake)
        info = fake.capture(provider_order)
        res = await call(
            app,
            f"{ORDERS}/{order_id}/verify",
            verify_body(fake, provider_order, info.provider_payment_id),
        )
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "captured", "available_minor": 100_000}
    [entry] = world.db.all(WalletLedger)
    assert (entry.entry_type, entry.amount_minor, entry.idempotency_key) == (
        "top_up",
        100_000,
        f"rzp:{info.provider_payment_id}",
    )
    [payment] = world.db.all(Payment)
    assert payment.status == "captured" and payment.source == "checkout"
    [order] = world.db.all(PaymentOrder)
    assert order.status == "paid"
    [audit] = world.audits("billing.payment_received")
    assert audit.actor_kind == "user" and audit.details["source"] == "checkout"
    assert [m[0] for m in world.mail] == ["payment_received"]
    assert world.mail[0][1] == "asha@shree.example"


async def test_verifying_twice_credits_once(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        order_id, provider_order = await order_for(app, fake)
        info = fake.capture(provider_order)
        body = verify_body(fake, provider_order, info.provider_payment_id)
        first = await call(app, f"{ORDERS}/{order_id}/verify", body)
        second = await call(app, f"{ORDERS}/{order_id}/verify", body)
    assert first.json()["status"] == second.json()["status"] == "captured"
    assert len(world.db.all(WalletLedger)) == 1 and len(world.mail) == 1


async def test_a_wrong_signature_is_400_and_nothing_is_written(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        order_id, provider_order = await order_for(app, fake)
        info = fake.capture(provider_order)
        calls_before = len(fake.api_calls)
        res = await call(
            app,
            f"{ORDERS}/{order_id}/verify",
            {"provider_payment_id": info.provider_payment_id, "signature": "0" * 64},
        )
    assert res.status_code == 400 and res.json()["code"] == "payments_signature_invalid"
    assert not world.db.all(WalletLedger) and not world.db.all(Payment)
    assert len(fake.api_calls) == calls_before  # the provider was not even asked


async def test_a_signature_for_another_payment_is_refused(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        order_id, provider_order = await order_for(app, fake)
        info = fake.capture(provider_order)
        other = fake.sign_checkout(provider_order, "pay_someoneelse")
        res = await call(
            app,
            f"{ORDERS}/{order_id}/verify",
            {"provider_payment_id": info.provider_payment_id, "signature": other},
        )
    assert res.status_code == 400 and not world.db.all(WalletLedger)


async def test_another_workspaces_order_is_a_404(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        order_id, provider_order = await order_for(app, fake)
        info = fake.capture(provider_order)
        res = await call(
            app,
            f"{ORDERS}/{order_id}/verify",
            verify_body(fake, provider_order, info.provider_payment_id),
            email="meena@pune.example",
        )
        unknown = await call(
            app,
            f"{ORDERS}/00000000-0000-0000-0000-000000000009/verify",
            verify_body(fake, provider_order, info.provider_payment_id),
        )
    assert res.status_code == 404 and res.json()["code"] == "order_not_found"
    assert unknown.status_code == 404 and not world.db.all(WalletLedger)


async def test_an_authorized_payment_is_pending_not_credited(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        order_id, provider_order = await order_for(app, fake)
        info = fake.authorize(provider_order)
        res = await call(
            app,
            f"{ORDERS}/{order_id}/verify",
            verify_body(fake, provider_order, info.provider_payment_id),
        )
    assert res.json() == {"status": "pending", "available_minor": None}
    assert not world.db.all(WalletLedger)


async def test_a_provider_outage_is_pending_and_the_webhook_can_finish(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        order_id, provider_order = await order_for(app, fake)
        info = fake.capture(provider_order)
        fake.down = ProviderError("unreachable", "down")
        res = await call(
            app,
            f"{ORDERS}/{order_id}/verify",
            verify_body(fake, provider_order, info.provider_payment_id),
        )
    assert res.status_code == 200 and res.json()["status"] == "pending"
    assert not world.db.all(WalletLedger)


async def test_a_forged_amount_is_never_credited(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        order_id, provider_order = await order_for(app, fake)
        info = fake.capture(provider_order, amount_minor=1_000)
        res = await call(
            app,
            f"{ORDERS}/{order_id}/verify",
            verify_body(fake, provider_order, info.provider_payment_id),
        )
    assert res.json()["status"] == "pending" and not world.db.all(WalletLedger)
    [payment] = world.db.all(Payment)
    assert payment.status == "mismatch"
    assert [(m[0], m[1]) for m in world.mail] == [("payment_attention", "ops@operator.example")]


async def test_a_member_cannot_verify(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        order_id, _ = await order_for(app, fake)
        res = await call(
            app,
            f"{ORDERS}/{order_id}/verify",
            {"provider_payment_id": "pay_x123", "signature": "0" * 64},
            email="ravi@shree.example",
        )
    assert res.status_code == 403 and res.json()["code"] == "owner_required"
