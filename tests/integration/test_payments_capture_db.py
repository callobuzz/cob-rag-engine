"""Capture on a real PostgreSQL (P1 T6): one payment is one ledger entry, whatever races.

Real ledger, real locks, the fake provider. Needs Docker.
"""

import asyncio
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from callobuzz_rag.accounts.settings_store import (
    invalidate_engine_settings_cache,
    read_engine_settings_row,
    update_engine_settings,
)
from callobuzz_rag.billing import accounts
from callobuzz_rag.billing.payments import capture, documents, orders
from callobuzz_rag.billing.payments.capture import apply_captured
from callobuzz_rag.billing.payments.fake import FakePaymentProvider
from callobuzz_rag.db.models.add_on import AddOn, AddOnGrant
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.payment import BillingProfile, Payment, PaymentOrder
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from tests.integration.conftest import new_tenant_id

pytestmark = pytest.mark.integration


@dataclass
class Owner:
    tenant_id: uuid.UUID
    account_id: uuid.UUID


@pytest.fixture
def factory(db_engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def seller(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await update_engine_settings(
            session,
            {
                "company": {"legal_name": "Callobuzz", "state_code": "27", "city": "Pune"},
                "invoicing": {"gstin": "27AAPFU0939F1ZV"},
            },
            actor_label="test",
        )
        await session.commit()
    invalidate_engine_settings_cache()


async def make_owner(factory: async_sessionmaker[AsyncSession], *, state: str = "27") -> Owner:
    await seller(factory)
    async with factory() as session:
        tenant_id = await new_tenant_id(session)
        account = await accounts.ensure_account(session, tenant_id)
        session.add(
            BillingProfile(
                tenant_id=tenant_id,
                legal_name="Sharma Furniture",
                address_line1="Paud Road, Kothrud",
                city="Pune",
                state_code=state,
                country="IN",
            )
        )
        await session.commit()
        return Owner(tenant_id, account.id)


async def start(
    factory: async_sessionmaker[AsyncSession],
    fake: FakePaymentProvider,
    owner: Owner,
    *,
    purpose: str = "top_up",
    amount: int | None = 100_000,
    add_on_id: uuid.UUID | None = None,
) -> PaymentOrder:
    async with factory() as session:
        settings = (await read_engine_settings_row(session))[0]
        order, _, _ = await orders.start_order(
            session,
            fake,
            owner.tenant_id,
            settings,
            purpose=purpose,  # type: ignore[arg-type]
            amount_minor=amount,
            add_on_id=add_on_id,
            idempotency_key=f"k-{uuid.uuid4().hex[:16]}",
            created_by="asha@shree.example",
        )
        await session.commit()
        return order


async def apply(
    factory: async_sessionmaker[AsyncSession], info: Any, source: str = "webhook"
) -> capture.CaptureResult:
    async with factory() as session:
        result = await apply_captured(session, info, source=source)  # type: ignore[arg-type]
        await session.commit()
        return result


async def ledger_rows(
    factory: async_sessionmaker[AsyncSession], owner: Owner
) -> list[WalletLedger]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(WalletLedger)
                    .where(WalletLedger.account_id == owner.account_id)
                    .order_by(WalletLedger.seq)
                )
            )
            .scalars()
            .all()
        )


async def balance(factory: async_sessionmaker[AsyncSession], owner: Owner) -> tuple[int, int]:
    """(cached balance, Σ ledger): invariant I1 says they are equal."""
    async with factory() as session:
        cached = (
            await session.execute(
                select(BillingAccount.balance_minor).where(BillingAccount.id == owner.account_id)
            )
        ).scalar_one()
        total = (
            await session.execute(
                select(func.coalesce(func.sum(WalletLedger.amount_minor), 0)).where(
                    WalletLedger.account_id == owner.account_id
                )
            )
        ).scalar_one()
        return int(cached), int(total)


async def audits(factory: async_sessionmaker[AsyncSession], owner: Owner) -> list[AuditLog]:
    async with factory() as session:
        return list(
            (await session.execute(select(AuditLog).where(AuditLog.tenant_id == owner.tenant_id)))
            .scalars()
            .all()
        )


async def payment_of(factory: async_sessionmaker[AsyncSession], pay_id: str) -> Payment | None:
    async with factory() as session:
        return (
            await session.execute(select(Payment).where(Payment.provider_payment_id == pay_id))
        ).scalar_one_or_none()


async def test_top_up_capture_is_one_entry_keyed_by_payment(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "", method="upi")
    result = await apply(factory, info)
    assert result.created and result.status == "credited"
    [entry] = await ledger_rows(factory, owner)
    assert (entry.entry_type, entry.amount_minor, entry.idempotency_key) == (
        "top_up",
        100_000,
        f"rzp:{info.provider_payment_id}",
    )
    payment = await payment_of(factory, info.provider_payment_id)
    assert payment is not None
    assert (payment.gross_minor, payment.taxable_minor, payment.cgst_minor, payment.sgst_minor) == (
        118_000,
        100_000,
        9_000,
        9_000,
    )
    assert payment.entry_id == entry.id and payment.status == "captured"
    assert (payment.fee_minor, payment.fee_tax_minor) == (info.fee_minor, info.tax_minor)
    assert await balance(factory, owner) == (100_000, 100_000)
    async with factory() as session:
        stored = await session.get(PaymentOrder, order.id)
        assert stored is not None and stored.status == "paid" and stored.paid_at is not None
    [row] = [a for a in await audits(factory, owner) if a.action == "billing.payment_received"]
    assert row.details["source"] == "webhook" and row.details["gross_minor"] == 118_000


async def test_checkout_verify_and_webhook_race_credit_once(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner, amount=50_000)
    info = fake.capture(order.provider_order_id or "")
    results = await asyncio.gather(
        apply(factory, info, "checkout"), apply(factory, info), apply(factory, info)
    )
    assert sorted(r.created for r in results) == [False, False, True]
    rows = await ledger_rows(factory, owner)
    assert [r.amount_minor for r in rows] == [50_000]
    assert await balance(factory, owner) == (50_000, 50_000)
    async with factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(Payment)
                .where(Payment.provider_payment_id == info.provider_payment_id)
            )
        ).scalar_one()
    assert count == 1


async def test_amount_mismatch_is_never_credited(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = replace(fake.capture(order.provider_order_id or ""), amount_minor=1_000)
    result = await apply(factory, info)
    assert not result.created and result.status == "mismatch"
    assert await ledger_rows(factory, owner) == []
    payment = await payment_of(factory, info.provider_payment_id)
    assert payment is not None and payment.status == "mismatch"
    assert [a.action for a in await audits(factory, owner)] == ["billing.payment_mismatch"]
    assert result.after and result.after[0].kind == "attention"
    # replaying it never credits either
    again = await apply(factory, info)
    assert again.status == "duplicate" and await ledger_rows(factory, owner) == []


async def test_a_second_payment_for_a_paid_order_is_a_mismatch(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    first = fake.capture(order.provider_order_id or "")
    second = fake.capture(order.provider_order_id or "")
    assert (await apply(factory, first)).status == "credited"
    assert (await apply(factory, second)).status == "mismatch"
    assert len(await ledger_rows(factory, owner)) == 1


async def make_add_on(factory: Any, **values: Any) -> AddOn:
    base: dict[str, Any] = {
        "name": f"Add-on {uuid.uuid4().hex[:8]}",
        "currency": "INR",
        "bonus_pct": 0,
        "is_active": True,
    }
    async with factory() as session:
        row = AddOn(**{**base, **values})
        session.add(row)
        await session.commit()
        return row


async def test_credit_pack_online_is_one_add_on_entry(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    pack = await make_add_on(
        factory,
        kind="credit_pack",
        price_minor=100_000,
        credit_minor=100_000,
        bonus_pct=10,
    )
    order = await start(factory, fake, owner, purpose="add_on", amount=None, add_on_id=pack.id)
    info = fake.capture(order.provider_order_id or "")
    await apply(factory, info)
    [entry] = await ledger_rows(factory, owner)
    assert (entry.entry_type, entry.amount_minor, entry.idempotency_key) == (
        "add_on_purchase",
        110_000,
        f"rzp:{info.provider_payment_id}",
    )
    async with factory() as session:
        grants = list(
            (
                await session.execute(
                    select(AddOnGrant).where(AddOnGrant.tenant_id == owner.tenant_id)
                )
            )
            .scalars()
            .all()
        )
    [grant] = grants
    assert (grant.payment, grant.provider_payment_id) == ("gateway", info.provider_payment_id)
    assert await balance(factory, owner) == (110_000, 110_000)


async def test_question_pack_online_tops_up_then_buys_even_when_negative(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    async with factory() as session:  # the workspace owes ₹500
        from callobuzz_rag.billing import ledger

        await ledger.append_entries(
            session,
            owner.account_id,
            [ledger.NewEntry(ledger.EntryType.ADJUSTMENT, -50_000, "owe-500", memo="owes")],
            actor=ledger.SYSTEM_ACTOR,
        )
        await session.commit()
    pack = await make_add_on(factory, kind="question_pack", price_minor=29_900, questions=500)
    order = await start(factory, fake, owner, purpose="add_on", amount=None, add_on_id=pack.id)
    info = fake.capture(order.provider_order_id or "")
    await apply(factory, info)
    rows = await ledger_rows(factory, owner)
    assert [(r.entry_type, r.amount_minor) for r in rows][-2:] == [
        ("top_up", 29_900),
        ("add_on_purchase", -29_900),
    ]
    assert rows[-2].idempotency_key == f"rzp:{info.provider_payment_id}"
    assert await balance(factory, owner) == (-50_000, -50_000)  # funded: never refused


async def test_an_add_on_that_vanished_still_credits_the_money(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    pack = await make_add_on(factory, kind="credit_pack", price_minor=100_000, credit_minor=100_000)
    order = await start(factory, fake, owner, purpose="add_on", amount=None, add_on_id=pack.id)
    async with factory() as session:  # deactivated between the order and the payment
        row = await session.get(AddOn, pack.id)
        assert row is not None
        row.is_active = False
        await session.commit()
    info = fake.capture(order.provider_order_id or "")
    result = await apply(factory, info)
    [entry] = await ledger_rows(factory, owner)
    assert (entry.entry_type, entry.amount_minor) == ("top_up", 100_000)
    assert result.created and any(a.kind == "attention" for a in result.after)


async def test_authorized_is_recorded_and_credited_once_it_is_captured(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.authorize(order.provider_order_id or "")
    assert (await apply(factory, info)).status == "authorized"
    assert await ledger_rows(factory, owner) == []
    captured = replace(info, status="captured", captured_at=datetime.now(UTC))
    assert (await apply(factory, captured, "sweep")).status == "credited"
    assert (await apply(factory, captured, "sweep")).status == "duplicate"
    assert len(await ledger_rows(factory, owner)) == 1


async def test_a_failed_payment_credits_nothing(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.fail(order.provider_order_id or "")
    assert (await apply(factory, info)).status == "not_captured"
    assert await ledger_rows(factory, owner) == []


async def test_late_capture_of_expired_order_is_still_credited(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    async with factory() as session:
        row = await session.get(PaymentOrder, order.id)
        assert row is not None
        row.status = "expired"
        row.expires_at = datetime.now(UTC) - timedelta(days=2)
        await session.commit()
    info = fake.capture(order.provider_order_id or "")
    assert (await apply(factory, info)).status == "credited"
    [row_audit] = [
        a for a in await audits(factory, owner) if a.action == "billing.payment_received"
    ]
    assert row_audit.details["late"] is True
    assert await balance(factory, owner) == (100_000, 100_000)


async def test_a_payment_with_no_order_is_reported_not_credited(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = replace(
        fake.capture(order.provider_order_id or ""), provider_order_id="order_nobody", notes={}
    )
    result = await apply(factory, info)
    assert result.status == "unknown_order" and result.after[0].kind == "attention"
    assert await ledger_rows(factory, owner) == []


async def test_the_documents_seam_runs_inside_the_transaction(
    factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "")
    seen: dict[str, Any] = {}

    async def boom(
        session: AsyncSession, payment: Payment, order: PaymentOrder, *, now: Any = None
    ) -> None:
        # the ledger entry and the payment are visible here, before the commit
        seen["entry"] = payment.entry_id
        seen["rows"] = (
            await session.execute(
                select(func.count())
                .select_from(WalletLedger)
                .where(WalletLedger.account_id == payment.account_id)
            )
        ).scalar_one()
        raise RuntimeError("invoice failed")

    monkeypatch.setattr(documents, "on_payment_captured", boom)
    with pytest.raises(RuntimeError):
        await apply(factory, info)
    assert seen["entry"] is not None and seen["rows"] == 1
    # everything rolled back together: no ledger entry, no payment, the order still open
    assert await ledger_rows(factory, owner) == []
    assert await payment_of(factory, info.provider_payment_id) is None
    async with factory() as session:
        stored = await session.get(PaymentOrder, order.id)
        assert stored is not None and stored.status == "created"


async def test_after_commit_side_effects(factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from callobuzz_rag.accounts import lifecycle
    from callobuzz_rag.billing import hooks

    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    result = await apply(factory, fake.capture(order.provider_order_id or ""))
    hook = AsyncMock()
    monkeypatch.setattr(hooks, "after_balance_change", hook)
    monkeypatch.setattr(lifecycle, "owner_emails", AsyncMock(return_value=["asha@shree.example"]))
    mail: list[tuple[str, str, dict[str, Any]]] = []
    sent: list[tuple[Any, dict[str, Any]]] = []

    async def dispatch(event: Any, data: dict[str, Any]) -> None:
        sent.append((event, data))

    settings = SimpleNamespace(
        superadmin_email="ops@operator.example", dashboard_url="https://app.example.com"
    )
    await capture.run_after_commit(
        factory,
        settings,
        result,
        enqueue=lambda template, to, ctx, **kw: mail.append((template, to, dict(ctx))),
        dispatch=dispatch,
    )
    hook.assert_awaited_once()
    [(template, to, ctx)] = mail
    assert (template, to) == ("payment_received", "asha@shree.example")
    assert ctx["amount"] == "₹1,180.00" and ctx["balance"] == "₹1,000.00"
    [(event, data)] = sent
    assert event.value == "billing.payment_received" and data["gross_minor"] == 118_000
    assert "key" not in str(data).lower()


async def test_inclusive_question_pack_leaves_the_balance_unchanged(factory: Any) -> None:
    """With prices that include GST the order's taxable part is below the price; the purchase
    must take exactly what the payment topped up, so the owner's balance does not move."""
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    async with factory() as session:
        await update_engine_settings(
            session, {"invoicing": {"prices_include_gst": True}}, actor_label="test"
        )
        await session.commit()
    invalidate_engine_settings_cache()
    try:
        pack = await make_add_on(factory, kind="question_pack", price_minor=29_900, questions=500)
        order = await start(factory, fake, owner, purpose="add_on", amount=None, add_on_id=pack.id)
        assert order.taxable_minor < 29_900 and order.gross_minor == 29_900
        info = fake.capture(order.provider_order_id or "")
        await apply(factory, info)
    finally:  # the database is shared by the whole session: put the setting back
        async with factory() as session:
            await update_engine_settings(
                session, {"invoicing": {"prices_include_gst": False}}, actor_label="test"
            )
            await session.commit()
        invalidate_engine_settings_cache()
    rows = await ledger_rows(factory, owner)
    assert [r.entry_type for r in rows] == ["top_up", "add_on_purchase"]
    assert rows[0].amount_minor == order.taxable_minor
    assert rows[1].amount_minor == -order.taxable_minor
    assert await balance(factory, owner) == (0, 0)


async def test_price_edited_after_the_order_does_not_move_the_balance(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    pack = await make_add_on(factory, kind="question_pack", price_minor=29_900, questions=500)
    order = await start(factory, fake, owner, purpose="add_on", amount=None, add_on_id=pack.id)
    async with factory() as session:
        row = await session.get(AddOn, pack.id)
        assert row is not None
        row.price_minor = 39_900
        await session.commit()
    await apply(factory, fake.capture(order.provider_order_id or ""))
    assert await balance(factory, owner) == (0, 0)
