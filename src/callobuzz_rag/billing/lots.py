"""Credit lots: credit that expires (included credit, packs, goodwill), and how usage uses it up.

The pure functions (`consume`, `expire_amount`) are what the database code and the reference
model in the tests both call. Only a usage charge consumes lots, earliest expiry first, so the
plan's included credit is spent before anything the customer paid for. An expiring lot expires
`min(remaining, max(balance, 0))`: money that is not there anymore (spent, refunded) is never
expired a second time, and a negative balance expires nothing.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.credit_lot import CreditLot


@dataclass(frozen=True, slots=True)
class Lot:
    """One lot as the pure functions see it."""

    id: uuid.UUID | int | str
    amount: int
    remaining: int
    expires_at: datetime | int


def consume[L: Lot](lots: Sequence[L], amount: int) -> tuple[list[L], int]:
    """Use up `amount` minor units of `lots`, earliest expiry first.

    Returns the lots in their input order with their new `remaining`, and how much was used
    (at most `amount`, at most what the lots held)."""
    if amount < 0:
        raise ValueError("consume takes a positive amount")
    order = sorted(range(len(lots)), key=lambda i: (lots[i].expires_at, i))
    left = amount
    out = list(lots)
    for i in order:
        if left == 0:
            break
        lot = out[i]
        take = min(lot.remaining, left)
        if take:
            out[i] = replace(lot, remaining=lot.remaining - take)
            left -= take
    return out, amount - left


def expire_amount(remaining: int, balance: int) -> int:
    """What a lot that reached its expiry actually removes from the balance."""
    return max(0, min(remaining, max(balance, 0)))


async def live_lots(session: AsyncSession, account_id: uuid.UUID) -> list[CreditLot]:
    """The account's lots with credit left, earliest expiry first, locked for update."""
    return list(
        (
            await session.execute(
                select(CreditLot)
                .where(CreditLot.account_id == account_id, CreditLot.remaining_minor > 0)
                .order_by(CreditLot.expires_at, CreditLot.created_at, CreditLot.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def consume_in_db(session: AsyncSession, account_id: uuid.UUID, amount: int) -> int:
    """`consume` against the stored lots; returns what was used."""
    lots = await live_lots(session, account_id)
    pure = [Lot(lot.id, lot.amount_minor, lot.remaining_minor, lot.expires_at) for lot in lots]
    updated, used = consume(pure, amount)
    for row, new in zip(lots, updated, strict=True):
        if row.remaining_minor != new.remaining:
            row.remaining_minor = new.remaining
    if used:
        await session.flush()
    return used


async def live_credit(session: AsyncSession, account_id: uuid.UUID) -> int:
    """The sum of credit still in lots (promotional money, never refundable cash)."""
    from sqlalchemy import func

    return int(
        (
            await session.execute(
                select(func.coalesce(func.sum(CreditLot.remaining_minor), 0)).where(
                    CreditLot.account_id == account_id
                )
            )
        ).scalar_one()
    )
