"""The wallet ledger: every money movement is one appended row, the balance is their sum.

Invariants (property-tested against `tests/unit/billing_model.py`, and again on PostgreSQL):

* I1  `billing_accounts.balance_minor == sum(wallet_ledger.amount_minor)`, `seq` runs 1..n
      without gaps, and each row's `balance_after_minor` is the running sum in `seq` order.
* I2  Rows are never updated or deleted (a trigger refuses it; only the delete of the whole
      workspace cascades). A mistake is corrected with a new `adjustment` or `refund`.
* I3  `append_entries` locks the account row first, so top-ups, postings, subscriptions and
      expiries interleave safely.
* I4  `(account_id, idempotency_key)` is unique: the same key with the same type and amount is
      a no-op, with a different one it is a 409 `idempotency_conflict`.

`append_entries` never commits; the caller commits (with its audit row).
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import Actor
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.lots import consume_in_db
from callobuzz_rag.billing.money import MoneyError
from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.credit_lot import CreditLot
from callobuzz_rag.db.models.wallet_ledger import WalletLedger

SYSTEM_ACTOR: Final[Actor] = ("system", None, "system")


class EntryType(StrEnum):
    TOP_UP = "top_up"  # + money received (manual now, a gateway later)
    SUBSCRIPTION_CHARGE = "subscription_charge"  # - the period's fee
    USAGE_CHARGE = "usage_charge"  # - a posted batch of usage charges
    ADD_ON_PURCHASE = "add_on_purchase"  # + credit pack / - add-on paid from the balance
    ADJUSTMENT = "adjustment"  # +/- a correction (memo required)
    REFUND = "refund"  # - money returned to the customer
    INCLUDED_CREDIT = "included_credit"  # + the plan's included credit for a period
    EXPIRY = "expiry"  # - unused expiring credit


# +1 positive only, -1 negative only, 0 either sign (an amount is never 0).
SIGN: Final[dict[EntryType, int]] = {
    EntryType.TOP_UP: 1,
    EntryType.SUBSCRIPTION_CHARGE: -1,
    EntryType.USAGE_CHARGE: -1,
    EntryType.ADD_ON_PURCHASE: 0,
    EntryType.ADJUSTMENT: 0,
    EntryType.REFUND: -1,
    EntryType.INCLUDED_CREDIT: 1,
    EntryType.EXPIRY: -1,
}

DEFAULT_MEMO_PUBLIC: Final[dict[EntryType, str]] = {
    EntryType.TOP_UP: "Top-up",
    EntryType.SUBSCRIPTION_CHARGE: "Subscription",
    EntryType.USAGE_CHARGE: "Usage",
    EntryType.ADD_ON_PURCHASE: "Add-on",
    EntryType.ADJUSTMENT: "Adjustment",
    EntryType.REFUND: "Refund",
    EntryType.INCLUDED_CREDIT: "Included credit",
    EntryType.EXPIRY: "Expired credit",
}


class LedgerError(RAGKernelError):
    """A ledger entry that cannot be written."""


class IdempotencyConflict(APIError):  # noqa: N818 (the pinned name)
    """The same idempotency key was used for a different type or amount."""

    def __init__(self, key: str) -> None:
        super().__init__(
            409,
            "That request was already used for a different transaction. Start a new one.",
            "idempotency_conflict",
            idempotency_key=key,
        )


@dataclass(frozen=True)
class NewEntry:
    entry_type: EntryType
    amount_minor: int
    idempotency_key: str  # required: "topup:<uuid>", "sub:<period start>", "usage:<batch>", ...
    reference_type: str | None = None
    reference_id: str | None = None
    memo: str | None = None  # admin only, <= 500
    memo_public: str | None = None  # what the customer sees, <= 200
    period: date | None = None  # first day of the month it belongs to
    expires_at: datetime | None = None  # positive entries only: creates a credit lot


@dataclass(frozen=True)
class AppendResult:
    entry_id: uuid.UUID
    seq: int
    balance_after_minor: int
    created: bool  # False: the same key, type and amount were already there


def check_sign(entry_type: EntryType, amount_minor: int) -> None:
    """Raises `MoneyError` for a zero amount, a non-integer, or the wrong sign for the type."""
    if type(amount_minor) is not int:
        raise MoneyError("A ledger amount is a whole number of minor units.")
    if amount_minor == 0:
        raise MoneyError("A ledger amount is never zero.")
    sign = SIGN[entry_type]
    if (sign > 0 and amount_minor < 0) or (sign < 0 and amount_minor > 0):
        raise MoneyError(
            f"A {entry_type.value} entry must be {'positive' if sign > 0 else 'negative'}."
        )


def replay(amounts: Iterable[int], start: int = 0) -> list[int]:
    """`balance_after` for each amount in order: the running sum from `start`."""
    out: list[int] = []
    balance = start
    for amount in amounts:
        balance += amount
        out.append(balance)
    return out


def month_of(moment: datetime) -> date:
    """The first day of `moment`'s UTC month."""
    return moment.astimezone(UTC).date().replace(day=1)


async def append_entries(
    session: AsyncSession,
    account_id: uuid.UUID,
    entries: Sequence[NewEntry],
    *,
    actor: Actor,
    now: datetime | None = None,
) -> list[AppendResult]:
    """Append `entries` to the account's ledger, in order, in the caller's transaction.

    Locks the account row first. A repeated `idempotency_key` with the same type and amount
    returns the existing entry (`created=False`); with a different one it raises
    `IdempotencyConflict`. A positive entry with `expires_at` also creates a credit lot, and a
    `usage_charge` consumes lots (earliest expiry first). Does not commit.

    Raises:
        LedgerError: the account does not exist.
        MoneyError: a zero amount or the wrong sign for the entry type.
        IdempotencyConflict: see above.
    """
    moment = now or datetime.now(UTC)
    account = (
        await session.execute(
            select(BillingAccount)
            .where(BillingAccount.id == account_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if account is None:
        raise LedgerError(f"Billing account {account_id} not found.")
    kind, user_id, label = actor
    results: list[AppendResult] = []
    for entry in entries:
        existing = (
            await session.execute(
                select(WalletLedger).where(
                    WalletLedger.account_id == account_id,
                    WalletLedger.idempotency_key == entry.idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.entry_type != entry.entry_type.value
                or existing.amount_minor != entry.amount_minor
            ):
                raise IdempotencyConflict(entry.idempotency_key)
            results.append(
                AppendResult(existing.id, existing.seq, existing.balance_after_minor, False)
            )
            continue
        check_sign(entry.entry_type, entry.amount_minor)
        if entry.expires_at is not None and entry.amount_minor < 0:
            raise MoneyError("Only a positive entry can expire.")
        seq = account.ledger_seq + 1
        balance = account.balance_minor + entry.amount_minor
        row = WalletLedger(
            id=uuid.uuid4(),
            account_id=account_id,
            tenant_id=account.tenant_id,
            seq=seq,
            entry_type=entry.entry_type.value,
            amount_minor=entry.amount_minor,
            currency=account.currency,
            balance_after_minor=balance,
            idempotency_key=entry.idempotency_key,
            reference_type=entry.reference_type,
            reference_id=entry.reference_id,
            memo=entry.memo,
            memo_public=(entry.memo_public or DEFAULT_MEMO_PUBLIC[entry.entry_type])[:200],
            period=entry.period or month_of(moment),
            actor_kind=kind,
            actor_user_id=user_id,
            actor_label=label[:320],
            created_at=moment,
        )
        session.add(row)
        await session.flush()
        if entry.expires_at is not None:
            session.add(
                CreditLot(
                    account_id=account_id,
                    entry_id=row.id,
                    amount_minor=entry.amount_minor,
                    remaining_minor=entry.amount_minor,
                    expires_at=entry.expires_at,
                )
            )
        if entry.entry_type is EntryType.USAGE_CHARGE:
            await consume_in_db(session, account_id, -entry.amount_minor)
        if entry.amount_minor > 0 and entry.entry_type in (
            EntryType.TOP_UP,
            EntryType.ADD_ON_PURCHASE,
        ):
            account.last_top_up_minor = entry.amount_minor
        account.ledger_seq = seq
        account.balance_minor = balance
        results.append(AppendResult(row.id, seq, balance, True))
    account.updated_at = moment
    await session.flush()
    return results


# ---------------------------------------------------------------- what a customer can still use


async def owed_micro(session: AsyncSession, account: BillingAccount) -> int:
    """Usage already charged but not yet posted to the ledger, in account-currency micro units:
    the carried remainder plus every unposted charge (I5)."""
    from sqlalchemy import func

    from callobuzz_rag.db.models.usage_charge import UsageCharge

    pending = (
        await session.execute(
            select(func.coalesce(func.sum(UsageCharge.amount_micro), 0)).where(
                UsageCharge.account_id == account.id, UsageCharge.posted_at.is_(None)
            )
        )
    ).scalar_one()
    return int(account.usage_residue_micro) + int(pending)


async def currency_frozen(session: AsyncSession, account: BillingAccount) -> bool:
    """Whether the account's currency can no longer change: it has a transaction, or usage was
    charged in it that posting has not written yet (posting adds those amounts into the
    ledger's currency, so a switch in between would reinterpret them)."""
    return account.ledger_seq > 0 or await owed_micro(session, account) > 0


def available_minor_of(balance_minor: int, owed: int) -> int:
    """`floor((balance - owed) / 1 minor unit)`: what the account can still spend, counting
    usage the minute posting has not written yet (the lag never lets a customer overspend)."""
    from callobuzz_rag.billing.money import MICRO_PER_MINOR

    return (balance_minor * MICRO_PER_MINOR - owed) // MICRO_PER_MINOR


async def available_minor(session: AsyncSession, account: BillingAccount) -> int:
    return available_minor_of(account.balance_minor, await owed_micro(session, account))


async def paid_cash_minor(session: AsyncSession, account_id: uuid.UUID) -> int:
    """Money the customer paid in and has not had back: top-ups and the cash of add-on credit
    packs paid outside (the price, never the bonus on top), minus refunds. Credit that came free
    (included credit, goodwill, a free credit pack, a bonus) is never in it, so it cannot be
    refunded as cash even when it has no expiry and therefore no lot."""
    from sqlalchemy import String, case, cast, func, literal

    from callobuzz_rag.db.models.add_on import AddOnGrant

    pack_cash = case(
        (
            AddOnGrant.payment.in_(("external", "gateway")),  # P1 payments: paid online is cash
            func.least(WalletLedger.amount_minor, AddOnGrant.price_minor),
        ),
        else_=literal(0),
    )
    amount = case(
        (
            (WalletLedger.entry_type == EntryType.ADD_ON_PURCHASE.value)
            & (WalletLedger.amount_minor > 0),
            pack_cash,
        ),
        else_=WalletLedger.amount_minor,
    )
    paid: int = (
        await session.execute(
            select(func.coalesce(func.sum(amount), 0))
            .select_from(WalletLedger)
            .outerjoin(
                AddOnGrant,
                (WalletLedger.reference_type == "add_on_grant")
                & (cast(AddOnGrant.id, String) == WalletLedger.reference_id),
            )
            .where(
                WalletLedger.account_id == account_id,
                (WalletLedger.entry_type == EntryType.TOP_UP.value)
                | (
                    (WalletLedger.entry_type == EntryType.ADD_ON_PURCHASE.value)
                    & (WalletLedger.amount_minor > 0)
                )
                | (WalletLedger.entry_type == EntryType.REFUND.value),
            )
        )
    ).scalar_one()
    return int(paid)
