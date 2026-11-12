"""The operator's payment lists, refunds, payment links and event retry (P1 T8), and the owner's
own payment list. Through the real app on the in-memory database; the ledger is the recording
stand-in. The SQL and the races are proved on PostgreSQL in `tests/integration/test_payments_*`."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.settings_store import SETTINGS_ROW_ID, invalidate_engine_settings_cache
from callobuzz_rag.api.routes import payments_admin
from callobuzz_rag.billing import ledger, lots
from callobuzz_rag.billing.payments import events, refunds
from callobuzz_rag.billing.payments.base import ProviderError
from callobuzz_rag.billing.payments.fake import FakePaymentProvider
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.payment import (
    BillingProfile,
    Payment,
    PaymentEvent,
    PaymentOrder,
    PaymentRefund,
)
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req
from tests.unit.test_billing_ledger_api import fake_append

BASE = "/api/v1/admin/billing"


@pytest.fixture
def world() -> World:
    invalidate_engine_settings_cache()
    w = World()
    w.member(w.user("asha@shree.example"), w.shree, "owner")
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
    return w


def add_payment(
    world: World,
    tenant: Any,
    *,
    taxable: int = 100_000,
    age_days: int = 1,
    status: str = "captured",
) -> Payment:
    account = next(
        (a for a in world.db.all(BillingAccount) if a.tenant_id == tenant.id), None
    ) or world.db.add(
        BillingAccount(tenant_id=tenant.id, currency="INR", ledger_seq=0, balance_minor=taxable)
    )
    cgst = taxable * 9 // 100
    order = world.db.add(
        PaymentOrder(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            account_id=account.id,
            purpose="top_up",
            channel="checkout",
            currency="INR",
            taxable_minor=taxable,
            cgst_minor=cgst,
            sgst_minor=cgst,
            igst_minor=0,
            gross_minor=taxable + 2 * cgst,
            tax_kind="intra",
            place_of_supply="27",
            description="Add money to your balance",
            sac_code="998315",
            provider="razorpay",
            provider_order_id=f"order_{uuid.uuid4().hex[:10]}",
            status="paid",
            idempotency_key=uuid.uuid4().hex,
            created_by="asha@shree.example",
        )
    )
    return world.db.add(
        Payment(
            id=uuid.uuid4(),
            order_id=order.id,
            tenant_id=tenant.id,
            account_id=account.id,
            provider="razorpay",
            provider_payment_id=f"pay_{uuid.uuid4().hex[:12]}",
            method="upi",
            status=status,
            currency="INR",
            gross_minor=order.gross_minor,
            taxable_minor=taxable,
            cgst_minor=cgst,
            sgst_minor=cgst,
            igst_minor=0,
            refunded_minor=0,
            fee_minor=2_832,
            fee_tax_minor=432,
            captured_at=datetime.now(UTC) - timedelta(days=age_days),
            source="webhook",
            created_at=datetime.now(UTC) - timedelta(days=age_days),
        )
    )


@contextmanager
def app_for(world: World, fake: FakePaymentProvider | None = None) -> Iterator[Any]:
    async def refunded(session: Any, payment_id: uuid.UUID) -> int:  # the stand-in has no SUM()
        return sum(
            r.taxable_minor
            for r in world.db.all(PaymentRefund)
            if r.payment_id == payment_id and r.status in refunds.LIVE_STATUSES
        )

    with (
        patch.object(refunds, "refunded_taxable", refunded),
        auth_app(world) as app,
        fake_accounts(world.db),
        patch.object(ledger, "append_entries", fake_append(world)),
        patch.object(ledger, "available_minor", AsyncMock(return_value=100_000)),
        patch.object(ledger, "paid_cash_minor", AsyncMock(return_value=100_000)),
        patch.object(lots, "live_credit", AsyncMock(return_value=0)),
        patch.object(payments_admin, "events_for", AsyncMock(return_value=[])),
        patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()),
    ):
        app.state.payment_provider_override = fake or FakePaymentProvider()
        yield app


async def admin(app: Any, method: str, path: str, **kw: Any) -> Any:
    return await req(app, method, f"{BASE}{path}", key=ADMIN, **kw)


async def test_the_list_filters_pages_and_names_the_workspace(world: World) -> None:
    a = add_payment(world, world.shree)
    add_payment(world, world.pune, age_days=5)
    add_payment(world, world.shree, status="mismatch", age_days=3)
    with app_for(world) as app:
        everything = await admin(app, "GET", "/payments")
        mine = await admin(app, "GET", f"/payments?tenant_id={world.shree.id}")
        mismatches = await admin(app, "GET", "/payments?status=mismatch")
        page = await admin(app, "GET", "/payments?limit=1&offset=1")
        bad = await admin(app, "GET", "/payments?start=2026-10-09&end=2026-10-01")
    assert everything.json()["total"] == 3
    assert {i["tenant_name"] for i in everything.json()["items"]} == {"Shree Labs", "Pune Agency"}
    assert str(a.id) in [i["id"] for i in mine.json()["items"]] and mine.json()["total"] == 2
    assert mismatches.json()["total"] == 1 and page.status_code == 200  # paging: PostgreSQL
    row = next(i for i in everything.json()["items"] if i["id"] == str(a.id))
    assert (row["fee_minor"], row["fee_tax_minor"], row["tax_minor"]) == (2_832, 432, 18_000)
    assert bad.status_code == 422


async def test_the_detail_shows_what_can_still_be_refunded(world: World) -> None:
    pay = add_payment(world, world.shree)
    with app_for(world) as app:
        res = await admin(app, "GET", f"/payments/{pay.id}")
        missing = await admin(app, "GET", f"/payments/{uuid.uuid4()}")
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["refundable_minor"] == 100_000 and body["order_channel"] == "checkout"
    assert body["refunds"] == [] and body["events"] == []
    assert body["refund_deadline"] is not None
    assert missing.status_code == 404 and missing.json()["code"] == "payment_not_found"


REFUND = {
    "amount_minor": 40_000,
    "memo": "Customer cancelled",
    "idempotency_key": "refund-key-0001",
}


async def test_a_refund_takes_the_balance_first_then_asks_the_provider(world: World) -> None:
    pay = add_payment(world, world.shree)
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        res = await admin(app, "POST", f"/payments/{pay.id}/refunds", json=REFUND)
        again = await admin(app, "POST", f"/payments/{pay.id}/refunds", json=REFUND)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "pending" and body["taxable_minor"] == 40_000
    assert body["gross_minor"] == 47_200 and body["provider_refund_id"] in fake.refunds
    assert again.status_code == 200 and again.json()["id"] == body["id"]
    [entry] = [r for r in world.db.all(WalletLedger) if r.entry_type == "refund"]
    assert entry.amount_minor == -40_000 and entry.idempotency_key == f"refund:{body['id']}"
    assert fake.api_calls.count("refund") == 1  # the replay did not pay twice
    assert len(world.audits("billing.refund_requested")) == 1


async def test_a_provider_refusal_is_502_and_the_money_comes_back(world: World) -> None:
    pay = add_payment(world, world.shree)
    fake = FakePaymentProvider()
    fake.fail_refund_with = ProviderError("rejected", "no", status=400)
    with app_for(world, fake) as app:
        res = await admin(app, "POST", f"/payments/{pay.id}/refunds", json=REFUND)
    assert res.status_code == 502 and res.json()["code"] == "refund_failed"
    kinds = [(r.entry_type, r.amount_minor) for r in world.db.all(WalletLedger)]
    assert kinds == [("refund", -40_000), ("adjustment", 40_000)]
    [refund] = world.db.all(PaymentRefund)
    assert refund.status == "failed" and len(world.audits("billing.refund_failed")) == 1
    assert [m[0] for m in world.mail] == ["payment_attention"]


async def test_too_much_too_old_and_unconfirmed_refunds_are_refused(world: World) -> None:
    pay = add_payment(world, world.shree)
    old = add_payment(world, world.shree, age_days=200)
    big = add_payment(world, world.shree, taxable=5_000_000)
    with app_for(world) as app:
        over = await admin(
            app, "POST", f"/payments/{pay.id}/refunds", json={**REFUND, "amount_minor": 100_001}
        )
        late = await admin(app, "POST", f"/payments/{old.id}/refunds", json=REFUND)
        large = await admin(
            app,
            "POST",
            f"/payments/{big.id}/refunds",
            json={**REFUND, "amount_minor": 1_200_000, "idempotency_key": "refund-key-0002"},
        )
    assert over.status_code == 409 and over.json()["code"] == "refund_exceeds_refundable"
    assert over.json()["refundable_minor"] == 100_000
    assert late.status_code == 409 and late.json()["code"] == "refund_window_closed"
    assert large.status_code == 422 and large.json()["code"] == "confirm_mismatch"
    assert not world.db.all(WalletLedger)


async def test_a_payment_that_was_never_captured_cannot_be_refunded(world: World) -> None:
    pay = add_payment(world, world.shree, status="mismatch")
    with app_for(world) as app:
        res = await admin(app, "POST", f"/payments/{pay.id}/refunds", json=REFUND)
    assert res.status_code == 409 and res.json()["refundable_minor"] == 0


def tax_details(world: World, tenant: Any) -> None:
    world.db.add(
        BillingProfile(
            tenant_id=tenant.id,
            legal_name="Sharma Furniture",
            address_line1="Paud Road",
            city="Pune",
            state_code="27",
            country="IN",
            email_for_invoices="accounts@sharma.example",
        )
    )


async def test_a_payment_link_is_an_order_the_customer_can_open(world: World) -> None:
    tax_details(world, world.shree)
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        res = await admin(
            app,
            "POST",
            f"/accounts/{world.shree.id}/payment-links",
            json={"purpose": "top_up", "amount_minor": 100_000, "send_email": True},
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["short_url"].startswith("https://rzp.io/i/") and body["gross_minor"] == 118_000
    assert body["emailed"] is True
    [order] = world.db.all(PaymentOrder)
    assert (
        order.channel == "link" and order.link_id in fake.links and order.provider_order_id is None
    )
    held = fake.orders[fake.links[order.link_id]["order"]]
    assert held.notes["cob_order"] == str(order.id) and held.order.amount_minor == 118_000
    assert order.expires_at is not None and order.expires_at > datetime.now(UTC) + timedelta(days=6)
    assert [(m[0], m[1]) for m in world.mail] == [("payment_link", "asha@shree.example")]
    assert world.mail[0][2]["pay_url"] == body["short_url"]
    assert len(world.audits("billing.payment_link_created")) == 1


async def test_a_payment_link_without_tax_details_or_with_a_provider_outage(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake) as app:
        missing = await admin(
            app,
            "POST",
            f"/accounts/{world.shree.id}/payment-links",
            json={"purpose": "top_up", "amount_minor": 100_000},
        )
        tax_details(world, world.shree)
        fake.down = ProviderError("unreachable", "down")
        down = await admin(
            app,
            "POST",
            f"/accounts/{world.shree.id}/payment-links",
            json={"purpose": "top_up", "amount_minor": 100_000},
        )
        nobody = await admin(
            app, "POST", f"/accounts/{uuid.uuid4()}/payment-links", json={"purpose": "plan"}
        )
    assert missing.status_code == 409 and missing.json()["code"] == "tax_details_required"
    assert down.status_code == 502 and down.json()["code"] == "payments_unavailable"
    assert not world.db.all(PaymentOrder) and nobody.status_code == 404


async def test_failed_events_are_listed_and_can_be_retried(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued: list[str] = []
    monkeypatch.setattr(events, "queue_event", queued.append)
    failed = world.db.add(
        PaymentEvent(
            id=uuid.uuid4(),
            provider="razorpay",
            event_id="evt_failed",
            event="payment.captured",
            entity_ids={},
            payload_encrypted="x",
            status="failed",
            attempts=2,
            error="ProviderError:unreachable",
            received_at=datetime.now(UTC),
        )
    )
    done = world.db.add(
        PaymentEvent(
            id=uuid.uuid4(),
            provider="razorpay",
            event_id="evt_done",
            event="payment.captured",
            entity_ids={},
            payload_encrypted="x",
            status="done",
            attempts=1,
            received_at=datetime.now(UTC),
        )
    )
    with app_for(world) as app:
        listed = await admin(app, "GET", "/payments/events")
        retry = await admin(app, "POST", f"/payments/events/{failed.id}/retry")
        refused = await admin(app, "POST", f"/payments/events/{done.id}/retry")
        unknown = await admin(app, "POST", f"/payments/events/{uuid.uuid4()}/retry")
    items = listed.json()["items"]
    assert [i["event_id"] for i in items] == ["evt_failed"] and "payload" not in listed.text
    assert retry.status_code == 202 and retry.json() == {"queued": True}
    assert queued == ["evt_failed"] and failed.status == "pending"
    assert refused.status_code == 409 and refused.json()["code"] == "payment_event_not_retryable"
    assert unknown.status_code == 404


async def test_an_owner_sees_only_their_own_payments_and_never_a_fee(world: World) -> None:
    mine = add_payment(world, world.shree)
    add_payment(world, world.pune)
    add_payment(world, world.shree, status="mismatch")
    with app_for(world) as app:
        token = (await login(app, "asha@shree.example")).json()["access_token"]
        res = await req(app, "GET", "/api/v1/billing/payments", token=token)
        other = await req(
            app,
            "GET",
            "/api/v1/billing/payments",
            token=(await login(app, "meena@pune.example")).json()["access_token"],
        )
    body = res.json()
    assert res.status_code == 200 and body["total"] == 1
    item = body["items"][0]
    assert item["id"] == str(mine.id) and item["reference"] == mine.provider_payment_id[-6:]
    assert item["gross_minor"] == 118_000 and item["tax_minor"] == 18_000
    text = res.text.lower()
    assert "fee" not in text and "pay_" not in text and "order_" not in text
    assert other.json()["total"] == 1 and other.json()["items"][0]["id"] != str(mine.id)


async def test_an_unreachable_provider_is_202_and_the_refund_stays_requested(
    world: World,
) -> None:
    pay = add_payment(world, world.shree)
    fake = FakePaymentProvider()
    fake.fail_refund_with = ProviderError("unreachable", "ReadTimeout")
    with app_for(world, fake) as app:
        res = await admin(app, "POST", f"/payments/{pay.id}/refunds", json=REFUND)
    assert res.status_code == 202 and res.json()["status"] == "requested"
    kinds = [(r.entry_type, r.amount_minor) for r in world.db.all(WalletLedger)]
    assert kinds == [("refund", -40_000)]
    assert world.audits("billing.refund_failed") == []
