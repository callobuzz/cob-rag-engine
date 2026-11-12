"""R6 and the gateway fees in the P&L on a real PostgreSQL (P1 T9). Needs Docker."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from callobuzz_rag.billing import pnl, reconcile
from callobuzz_rag.billing.payments import recon
from callobuzz_rag.billing.payments.base import ProviderError, SettlementLine
from callobuzz_rag.billing.payments.fake import FakePaymentProvider
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.payment import Payment
from tests.integration.test_payments_capture_db import apply, balance, make_owner, payment_of, start

pytestmark = pytest.mark.integration

DAY = date(2026, 10, 7)
SETTLED_AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)


@pytest.fixture
def factory(db_engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


def settled(
    info: Any, *, amount: int | None = None, fee: int = 2_832, tax: int = 432
) -> SettlementLine:
    return SettlementLine(
        "payment",
        info.provider_payment_id,
        info.amount_minor if amount is None else amount,
        fee,
        tax,
        "setl_ABC123",
        SETTLED_AT,
    )


async def findings_for(
    f: async_sessionmaker[AsyncSession], fake: FakePaymentProvider, *, now: datetime | None = None
) -> tuple[list[reconcile.Finding], list[Any]]:
    async with f() as session:
        found, applied = await recon.settlement_findings(
            session, fake, DAY, now or datetime(2026, 10, 8, 3, 30, tzinfo=UTC)
        )
        await session.commit()
        return found, applied


def mine(findings: list[reconcile.Finding], *ids: str) -> list[reconcile.Finding]:
    """The findings about these payments (the database is shared with other tests)."""
    return [
        f
        for f in findings
        if f.detail.get("provider_payment_id") in ids or f.detail.get("provider_refund_id") in ids
    ]


async def test_a_lost_webhook_is_healed_and_still_reported(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "")  # settled at the provider, never applied
    fake.add_settlement(DAY, settled(info))
    found, applied = await findings_for(factory, fake)
    [r6a] = mine(found, info.provider_payment_id)
    assert r6a.check == "R6a" and r6a.detail["healed"] is True
    assert [r.status for r in applied] == ["credited"]
    assert await balance(factory, owner) == (100_000, 100_000)
    payment = await payment_of(factory, info.provider_payment_id)
    assert payment is not None and payment.source == "recon"
    assert (payment.fee_minor, payment.fee_tax_minor, payment.settlement_id) == (
        2_832,
        432,
        "setl_ABC123",
    )
    again, applied_again = await findings_for(factory, fake)
    assert mine(again, info.provider_payment_id) == [] and applied_again == []


async def test_fees_and_settlement_are_copied_once_and_idempotently(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "")
    await apply(factory, info)
    fake.add_settlement(DAY, settled(info, fee=2_400, tax=367))
    for _ in range(2):
        found, applied = await findings_for(factory, fake)
        assert mine(found, info.provider_payment_id) == [] and applied == []
    payment = await payment_of(factory, info.provider_payment_id)
    assert payment is not None
    assert (payment.fee_minor, payment.fee_tax_minor) == (2_400, 367)
    assert payment.settled_at == SETTLED_AT


async def test_r6c_a_different_amount_is_reported_and_left_alone(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "")
    await apply(factory, info)
    fake.add_settlement(DAY, settled(info, amount=100_000))
    found, _ = await findings_for(factory, fake)
    [r6c] = mine(found, info.provider_payment_id)
    assert r6c.check == "R6c" and r6c.detail["recorded_minor"] == 118_000
    payment = await payment_of(factory, info.provider_payment_id)
    assert payment is not None and payment.gross_minor == 118_000
    assert await balance(factory, owner) == (100_000, 100_000)


async def test_r6b_an_old_unsettled_payment_and_r6d_a_missing_entry(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "")
    await apply(factory, info)
    long_ago = datetime(2026, 10, 1, tzinfo=UTC)
    async with factory() as session:
        row = (
            await session.execute(
                select(Payment).where(Payment.provider_payment_id == info.provider_payment_id)
            )
        ).scalar_one()
        row.captured_at = long_ago
        await session.commit()
    found, _ = await findings_for(factory, fake)
    assert [f.check for f in mine(found, info.provider_payment_id)] == ["R6b"]
    # a payment whose ledger entry is gone (the entry id points at nothing)
    async with factory() as session:
        row = (
            await session.execute(
                select(Payment).where(Payment.provider_payment_id == info.provider_payment_id)
            )
        ).scalar_one()
        row.entry_id = None
        await session.commit()
    found, _ = await findings_for(factory, fake)
    assert sorted(f.check for f in mine(found, info.provider_payment_id)) == ["R6b", "R6d"]


async def test_a_refund_made_in_the_providers_dashboard_is_informational(factory: Any) -> None:
    fake = FakePaymentProvider()
    fake.add_settlement(
        DAY, SettlementLine("refund", "rfnd_dashboard_1", 5_000, 0, 0, "setl_1", SETTLED_AT)
    )
    found, _ = await findings_for(factory, fake)
    [r6e] = mine(found, "rfnd_dashboard_1")
    assert r6e.check == "R6e" and r6e.informational


async def test_an_unreachable_provider_is_one_informational_finding(factory: Any) -> None:
    fake = FakePaymentProvider()
    fake.down = ProviderError("unreachable", "down")
    found, applied = await findings_for(factory, fake)
    assert [(f.check, f.informational) for f in found] == [("R6f", True)] and applied == []


async def test_the_nightly_run_stores_r6_in_the_usual_row(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "")
    await apply(factory, info)
    fake.add_settlement(DAY, settled(info, amount=100_000))  # an amount that disagrees
    result = await reconcile.run(
        factory, day=DAY, now=datetime(2026, 10, 8, 3, 30, tzinfo=UTC), provider=fake
    )
    assert result is not None and not result.ok
    assert any(f.check == "R6c" for f in result.findings)
    async with factory() as session:
        audit = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.action == "billing.reconciliation_failed")
                    .order_by(AuditLog.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
    assert audit is not None
    # without a provider R6 is not run at all
    plain = await reconcile.run(factory, day=DAY, now=datetime(2026, 10, 8, 3, 30, tzinfo=UTC))
    assert plain is not None and not any(f.check.startswith("R6") for f in plain.findings)


async def test_gateway_fees_reach_the_pnl_per_customer_and_never_the_owner_view(
    factory: Any,
) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "")
    await apply(factory, info)
    this_month = datetime.now(UTC).date().replace(day=1)
    async with factory() as session:
        customers = await pnl.gather(session, this_month, this_month)
    [customer] = [c for c in customers if c.tenant_id == owner.tenant_id]
    assert customer.gateway_fee_minor == (info.fee_minor or 0) - (info.tax_minor or 0)
    assert customer.gateway_fee_tax_minor == info.tax_minor
    rows, totals = pnl.build_rows([customer], currency="INR", rate=83.5)
    assert rows[0].gateway_fee_minor == customer.gateway_fee_minor
    assert (
        totals.margin_minor
        == rows[0].billed_minor - rows[0].ai_cost_minor - rows[0].gateway_fee_minor
    )
    # a payment captured in another month is not counted here
    other = this_month - timedelta(days=40)
    async with factory() as session:
        before = await pnl.gather(session, other.replace(day=1), other.replace(day=1))
    assert owner.tenant_id not in [c.tenant_id for c in before]


async def test_a_gateway_credit_pack_counts_as_collected_cash(factory: Any) -> None:
    from callobuzz_rag.db.models.add_on import AddOn

    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    async with factory() as session:
        pack = AddOn(
            kind="credit_pack",
            name=f"Pack {datetime.now(UTC).timestamp()}",
            currency="INR",
            price_minor=100_000,
            credit_minor=100_000,
            bonus_pct=10,
            is_active=True,
        )
        session.add(pack)
        await session.commit()
        pack_id = pack.id
    order = await start(factory, fake, owner, purpose="add_on", amount=None, add_on_id=pack_id)
    await apply(factory, fake.capture(order.provider_order_id or ""))
    this_month = datetime.now(UTC).date().replace(day=1)
    async with factory() as session:
        customers = await pnl.gather(session, this_month, this_month)
    [customer] = [c for c in customers if c.tenant_id == owner.tenant_id]
    assert customer.collected_minor == 100_000  # the price, never the 10 % bonus
    from callobuzz_rag.billing import ledger

    async with factory() as session:
        assert await ledger.paid_cash_minor(session, owner.account_id) == 100_000
