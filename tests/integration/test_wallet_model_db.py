"""The pure wallet model and the real ledger on PostgreSQL must agree after every operation:
same balance, same entries, same lots. Needs Docker."""

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.db.models.credit_lot import CreditLot
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from tests.unit.billing_model import OPS, WalletModel

pytestmark = pytest.mark.integration

BASE = datetime(2030, 1, 1, tzinfo=UTC)


async def _new_account(factory) -> uuid.UUID:  # type: ignore[no-untyped-def]
    async with factory() as s:
        plan_id = (await s.execute(text("SELECT id FROM plans WHERE is_default"))).scalar_one()
        tenant = Tenant(name=f"wm-{uuid.uuid4().hex[:8]}", plan_id=plan_id)
        s.add(tenant)
        await s.flush()
        account = await ensure_account(s, tenant.id)
        await s.commit()
        return account.id


async def _compare(factory, account_id: uuid.UUID, model: WalletModel) -> None:  # type: ignore[no-untyped-def]
    async with factory() as s:
        rows = (
            (
                await s.execute(
                    select(WalletLedger)
                    .where(WalletLedger.account_id == account_id)
                    .order_by(WalletLedger.seq)
                )
            )
            .scalars()
            .all()
        )
        assert [
            (r.seq, r.entry_type, r.amount_minor, r.balance_after_minor, r.idempotency_key)
            for r in rows
        ] == [(e.seq, e.type.value, e.amount, e.balance_after, e.key) for e in model.entries]
        balance = (
            await s.execute(
                text("SELECT balance_minor FROM billing_accounts WHERE id = :a"), {"a": account_id}
            )
        ).scalar_one()
        assert balance == model.balance
        lots = {
            lot.entry_id: lot.remaining_minor
            for lot in (
                await s.execute(select(CreditLot).where(CreditLot.account_id == account_id))
            )
            .scalars()
            .all()
        }
        by_seq = {r.id: r.seq for r in rows}
        assert {by_seq[k]: v for k, v in lots.items()} == {
            int(str(lot.id).removeprefix("lot-")): lot.remaining for lot in model.lots
        }


@pytest.mark.parametrize("seed", range(20))
async def test_the_database_ledger_agrees_with_the_model(db_engine, seed: int) -> None:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    account_id = await _new_account(factory)
    rng = random.Random(seed)
    model = WalletModel("INR")
    before_lots: dict[str, int] = {}
    for step in range(30):
        op = rng.choice(OPS) if step else "top_up"
        seen = len(model.entries)
        before_lots = {str(lot.id): lot.remaining for lot in model.lots}
        model.apply(op, rng)
        async with factory() as s:
            for e in model.entries[seen:]:
                lot = next((x for x in model.lots if x.id == f"lot-{e.seq}"), None)
                expires = BASE + timedelta(days=int(lot.expires_at)) if lot is not None else None
                [result] = await append_entries(
                    s,
                    account_id,
                    [NewEntry(EntryType(e.type), e.amount, e.key, expires_at=expires)],
                    actor=SYSTEM_ACTOR,
                )
                assert result.created and result.seq == e.seq
            if op == "expire":  # the expiry job zeroes the lot it expired
                for lot in model.lots:
                    if lot.remaining == 0 and before_lots.get(str(lot.id), 0) > 0:
                        await s.execute(
                            text(
                                "UPDATE credit_lots SET remaining_minor = 0 WHERE entry_id = "
                                "(SELECT id FROM wallet_ledger WHERE account_id = :a AND seq = :q)"
                            ),
                            {"a": account_id, "q": int(str(lot.id).removeprefix("lot-"))},
                        )
            await s.commit()
        await _compare(factory, account_id, model)
