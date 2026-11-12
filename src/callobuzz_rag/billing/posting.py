"""Posting usage charges to the ledger, once a minute.

Charges are account-currency micro units (1e-6 of the major unit); the ledger is whole minor
units. A batch is the sum of the unposted charges plus the carried remainder, split into whole
minor units for the ledger and a new remainder (0..9999 micro) kept on the account, so no
fraction of a paisa is ever lost or invented (invariant I6, property-tested).
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update

from callobuzz_rag.billing import hooks
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.billing.money import split_micro
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.usage_charge import UsageCharge

logger = logging.getLogger(__name__)


def post_batch(amounts_micro: Sequence[int], residue_micro: int) -> tuple[int, int]:
    """`(minor, new_residue)` for a batch: `minor` whole minor units to write to the ledger
    (0 when the batch is still below one), `new_residue` carried to the next batch."""
    total = sum(amounts_micro) + residue_micro
    return split_micro(total)


# ---------------------------------------------------------------- the database side

BATCH_LIMIT = 5000


@dataclass(frozen=True)
class PostPlan:
    """What one batch writes: whole minor units for the ledger (0 = nothing to write yet), the
    carried remainder, and the line the customer reads."""

    minor: int
    residue: int
    answers: int
    memo_public: str


def plan_posting(amounts_micro: Sequence[int], questions: int, residue_micro: int) -> PostPlan:
    minor, rest = post_batch(amounts_micro, residue_micro)
    if questions:
        memo = f"Usage: {questions} answer{'s' if questions != 1 else ''}"
    else:
        memo = "Usage: assistant tasks"
    return PostPlan(minor, rest, questions, memo)


@dataclass(frozen=True)
class PostResult:
    account_id: uuid.UUID
    tenant_id: uuid.UUID
    rows: int
    minor: int  # written to the ledger (0 when the batch stayed below one minor unit)
    entry_id: uuid.UUID | None
    residue: int


async def post_account(
    session_factory: Any, account_id: uuid.UUID, *, now: datetime | None = None
) -> PostResult | None:
    """Post one account's pending charges in one transaction; None when there is nothing.

    Locks the account first (every ledger writer does), then the charge rows (`SKIP LOCKED`, so
    two posting workers never take the same row), turns their sum plus the carried remainder
    into one `usage_charge` entry, and marks the rows posted."""
    moment = now or datetime.now(UTC)
    async with session_factory() as session:
        account = (
            await session.execute(
                select(BillingAccount)
                .where(BillingAccount.id == account_id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if account is None:
            return None
        rows = (
            (
                await session.execute(
                    select(UsageCharge)
                    .where(
                        UsageCharge.account_id == account_id,
                        UsageCharge.posted_at.is_(None),
                        UsageCharge.currency == account.currency,
                    )
                    .order_by(UsageCharge.created_at, UsageCharge.request_id)
                    .limit(BATCH_LIMIT)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            stray = (
                await session.execute(
                    select(func.count())
                    .select_from(UsageCharge)
                    .where(
                        UsageCharge.account_id == account_id,
                        UsageCharge.posted_at.is_(None),
                        UsageCharge.currency != account.currency,
                    )
                )
            ).scalar_one()
            if stray:
                logger.error(
                    "Account %s has %s unposted usage charges in a currency other than %s; "
                    "they are left unposted",
                    account_id,
                    stray,
                    account.currency,
                )
            await session.rollback()
            return None
        plan = plan_posting(
            [r.amount_micro for r in rows],
            sum(r.questions for r in rows),
            account.usage_residue_micro,
        )
        entry_id: uuid.UUID | None = None
        if plan.minor > 0:
            batch = uuid.uuid4()
            [result] = await append_entries(
                session,
                account_id,
                [
                    NewEntry(
                        EntryType.USAGE_CHARGE,
                        -plan.minor,
                        f"usage:{batch}",
                        reference_type="usage_batch",
                        reference_id=str(batch),
                        memo_public=plan.memo_public,
                    )
                ],
                actor=SYSTEM_ACTOR,
                now=moment,
            )
            entry_id = result.entry_id
        account.usage_residue_micro = plan.residue
        await session.execute(
            update(UsageCharge)
            .where(UsageCharge.request_id.in_([r.request_id for r in rows]))
            .values(posted_at=moment, posted_entry_id=entry_id)
        )
        await session.commit()
        return PostResult(
            account_id, account.tenant_id, len(rows), plan.minor, entry_id, plan.residue
        )


async def post_pending(session_factory: Any, *, now: datetime | None = None) -> list[PostResult]:
    """Post every account's pending usage charges (the minute beat). One transaction per
    account; a failing account is logged and the others go on. After each commit the balance
    hook runs (BIL4 fills it)."""
    async with session_factory() as session:
        account_ids = list(
            (
                await session.execute(
                    select(UsageCharge.account_id).where(UsageCharge.posted_at.is_(None)).distinct()
                )
            )
            .scalars()
            .all()
        )
    results: list[PostResult] = []
    changed: list[uuid.UUID] = []
    for account_id in account_ids:
        try:
            while True:  # a backlog bigger than one batch is drained in turn
                result = await post_account(session_factory, account_id, now=now)
                if result is None:
                    break
                results.append(result)
                if result.minor:
                    changed.append(account_id)
                if result.rows < BATCH_LIMIT:
                    break
        except Exception as exc:
            logger.error("Posting usage for account %s failed (%s)", account_id, type(exc).__name__)
    if changed:
        try:
            await hooks.after_balance_change(session_factory, list(dict.fromkeys(changed)))
        except Exception as exc:
            logger.warning("Balance hook failed after posting (%s)", type(exc).__name__)
    return results
