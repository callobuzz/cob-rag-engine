"""Charging: what one metered request costs the customer, decided at answer time.

`charge_amount` is the pure price rule (tables in the tests); `charge_usage` runs inside
`analytics.usage.record_usage`, before the usage upsert, in a SAVEPOINT. It inserts one
`usage_charges` row keyed by the request id (`ON CONFLICT DO NOTHING`): a retry of the same
request returns `duplicate`, and `record_usage` then skips the usage upsert and the monthly
count too, so a request is neither counted nor charged twice. The minute posting
(`posting.py`) turns the unposted rows into ledger entries.

Amounts are account-currency micro units (1e-6 of the major unit), so an answer worth a
fraction of a paisa is neither rounded to zero nor to a whole paisa; `fx` (parts per million)
converts the recorded USD cost for a cost_plus charge and is stored on the row, so a charge can
be reproduced after the rate changes (decision D2).

For a chargeable account every metered event writes a row, amount 0 included (an included
question, an unknown cost_plus price, an answer made by the operator), so the daily
reconciliation can match the usage table one to one.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, Literal

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.config import billing_enabled, usd_inr_rate
from callobuzz_rag.billing.context import ADMIN_ANSWER, WAIVED_REASON  # PIP3 waiver
from callobuzz_rag.billing.money import (
    MICRO_PER_MINOR,
    Currency,
    fx_ppm,
    usd_micro_to_micro,
    with_markup,
)
from callobuzz_rag.billing.pricing import EffectivePricing, effective_pricing
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.usage_charge import UsageCharge

logger = logging.getLogger(__name__)

# Fixed namespace for request ids derived from a stable key (retried worker tasks).
NAMESPACE_BILLING: Final = uuid.UUID("6f1d5a52-3b8e-4c47-9f0a-b17c0b11a3d1")

PAYER_OPERATOR: Final = "operator"
PAYER_BYOK: Final = "byok"


@dataclass(frozen=True, slots=True)
class UsageEvent:
    request_id: uuid.UUID  # THE idempotency key: one answered request / one metered call group
    tenant_id: uuid.UUID
    corpus_id: uuid.UUID
    source: str
    payer: str  # operator | byok
    model: str
    questions: int  # record_usage `requests` (0 or 1)
    cost_usd_micro: int
    cost_known: bool
    questions_before: int  # the tenant's monthly count before this event
    at: datetime


@dataclass(frozen=True, slots=True)
class ChargeResult:
    # unbilled: billing off, exempt, no pricing, or not chargeable for this payer
    status: Literal["charged", "duplicate", "unbilled"]
    amount_micro: int
    overage: bool
    counts_toward_caps: bool = True  # False: a BYOK question the plan does not count


def _cost_plus(p: EffectivePricing, e: UsageEvent, fx: int) -> int:
    if not e.cost_known:
        return 0  # price unknown: charged 0 and flagged on the row (D4)
    return with_markup(usd_micro_to_micro(e.cost_usd_micro, fx), p.fields.markup_bp)


def charge_amount(
    p: EffectivePricing, e: UsageEvent, *, fx: int, monthly_limit: int | None
) -> tuple[int, bool]:
    """`(amount_micro, overage)` for one event. `overage` is True only when the question went
    past the monthly allowance and the plan charges for that (`overage = "charge"`); that price
    then replaces the base price for the question (D3). A `block` overage never reaches here
    (the cap refused the answer) and `allow` is the base price."""
    f = p.fields
    past_allowance = (
        e.questions > 0 and monthly_limit is not None and e.questions_before + 1 > monthly_limit
    )
    overage = past_allowance and f.overage == "charge"
    if e.payer == PAYER_BYOK:
        if f.byok_mode != "per_question":
            return 0, False
        # The customer's own key paid the provider: there is no cost to mark up, so even a
        # cost_plus overage uses the flat prices.
        if overage and f.byok_counts_toward_caps and f.overage_mode == "per_question":
            return e.questions * f.overage_per_question_minor * MICRO_PER_MINOR, True
        return e.questions * f.byok_per_question_minor * MICRO_PER_MINOR, False
    if overage:
        if f.overage_mode == "cost_plus":
            return _cost_plus(p, e, fx), True
        return f.overage_per_question_minor * MICRO_PER_MINOR * e.questions, True
    if f.usage_mode == "per_question":
        return e.questions * f.per_question_minor * MICRO_PER_MINOR, False
    if f.usage_mode == "cost_plus":
        return _cost_plus(p, e, fx), False
    return 0, False


async def charge_usage(session: AsyncSession, event: UsageEvent) -> ChargeResult:
    """Write the charge for `event` once. Does not commit; raises on a database problem (the
    caller, `record_usage`, logs it and goes on metering). Runs in a SAVEPOINT so a failed
    statement cannot poison the metering transaction."""
    if not billing_enabled():
        return ChargeResult("unbilled", 0, False)
    async with session.begin_nested():
        eff = await effective_pricing(session, event.tenant_id)
        counts = not (
            event.payer == PAYER_BYOK and not eff.fields.byok_counts_toward_caps and not eff.exempt
        )
        if not eff.chargeable(event.payer):
            return ChargeResult("unbilled", 0, False, counts)
        account = await ensure_account(session, event.tenant_id)
        if account.currency != eff.currency and account.ledger_seq == 0:
            # Until the first transaction the plan's currency is the truth.
            await session.execute(
                update(BillingAccount)
                .where(BillingAccount.id == account.id, BillingAccount.ledger_seq == 0)
                .values(currency=eff.currency)
            )
        fx = fx_ppm(usd_inr_rate(), eff.currency)
        # PIP3 waiver: the operator's own tests, or an answer given on the fallback profile
        # because the balance ran out (metered and counted, never billed)
        waived = "admin" if ADMIN_ANSWER.get() else WAIVED_REASON.get()
        # /PIP3 waiver
        if waived:
            amount, overage = 0, False
        else:
            from callobuzz_rag.accounts.caps import question_quota

            quota = (
                await question_quota(
                    session, event.tenant_id, event.at.date().replace(day=1), lock=True
                )
                if event.questions > 0
                else None
            )
            limit = quota[0] if quota is not None else None
            if quota is not None:  # the count as it stands inside this transaction
                event = replace(event, questions_before=quota[1])
            amount, overage = charge_amount(eff, event, fx=fx, monthly_limit=limit)
        inserted = (
            await session.execute(
                insert(UsageCharge)
                .values(
                    request_id=event.request_id,
                    account_id=account.id,
                    tenant_id=event.tenant_id,
                    corpus_id=event.corpus_id,
                    source=event.source[:64],
                    payer=event.payer,
                    model=event.model[:128],
                    questions=event.questions,
                    overage=overage,
                    cost_usd_micro=event.cost_usd_micro,
                    cost_known=event.cost_known,
                    pricing_id=eff.pricing_id,
                    usage_mode=eff.fields.usage_mode,
                    fx_ppm=fx,
                    amount_micro=amount,
                    waived=waived,
                    currency=eff.currency,
                    day=event.at.date(),
                )
                .on_conflict_do_nothing(index_elements=["request_id"])
                .returning(UsageCharge.request_id)
            )
        ).first()
    if inserted is None:
        return ChargeResult("duplicate", 0, False, counts)
    return ChargeResult("charged", amount, overage, counts)


# TV6 fees
PASS_THROUGH_MODE: Final = "pass_through"
SOURCE_WHATSAPP_FEE: Final = "whatsapp_fee"


async def charge_pass_through(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    tenant_id: uuid.UUID,
    corpus_id: uuid.UUID,
    price_micro: "Callable[[Currency], Awaitable[int | None]]",
    source: str = SOURCE_WHATSAPP_FEE,
    model: str = "",
    at: datetime | None = None,
) -> ChargeResult:
    """Charge a cost the deployer pays a third party on the workspace's behalf (Meta's fee for a
    WhatsApp message), once per `request_id`: one `usage_charges` row (no question, `usage_mode`
    `pass_through`), posted to the ledger like every other charge, so I1 to I6 hold unchanged.

    `price_micro` is asked the account's currency and answers the amount in micro units of it
    (the rates are per currency): None writes no row, 0 writes a row that costs nothing (a message
    inside a free allowance, which is counted). A workspace that is not billed (billing
    off, the operator's own, a system tenant) is not charged. Does not commit; runs in a SAVEPOINT
    like `charge_usage`, and raises on a database problem (the caller logs it: a fee that cannot
    be written must never fail a message that was sent).
    """
    if not billing_enabled():
        return ChargeResult("unbilled", 0, False)
    moment = at or datetime.now(UTC)
    async with session.begin_nested():
        eff = await effective_pricing(session, tenant_id)
        if eff.exempt:
            return ChargeResult("unbilled", 0, False)
        amount = await price_micro(eff.currency)
        if amount is None or amount < 0:
            return ChargeResult("unbilled", 0, False)
        account = await ensure_account(session, tenant_id)
        if account.currency != eff.currency and account.ledger_seq == 0:
            await session.execute(
                update(BillingAccount)
                .where(BillingAccount.id == account.id, BillingAccount.ledger_seq == 0)
                .values(currency=eff.currency)
            )
        inserted = (
            await session.execute(
                insert(UsageCharge)
                .values(
                    request_id=request_id,
                    account_id=account.id,
                    tenant_id=tenant_id,
                    corpus_id=corpus_id,
                    source=source[:64],
                    payer=PAYER_OPERATOR,
                    model=model[:128],
                    questions=0,
                    overage=False,
                    cost_usd_micro=0,
                    cost_known=True,
                    pricing_id=eff.pricing_id,
                    usage_mode=PASS_THROUGH_MODE,
                    fx_ppm=fx_ppm(usd_inr_rate(), eff.currency),
                    amount_micro=amount,
                    waived=None,
                    currency=eff.currency,
                    day=moment.date(),
                )
                .on_conflict_do_nothing(index_elements=["request_id"])
                .returning(UsageCharge.request_id)
            )
        ).first()
    if inserted is None:
        return ChargeResult("duplicate", 0, False)
    return ChargeResult("charged", amount, False)


# /TV6 fees
