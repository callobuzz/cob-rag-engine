"""The platform P&L: what each customer was billed and paid against what their answers cost the
operator's own AI key (BIL6). Super admin only; the customer never sees any of it.

Per customer over a range of UTC calendar months (the ledger's `period`):

* **billed**: the money charged for service, `-sum` of subscription charges, usage charges and
  add-ons paid from the balance. Adjustments, refunds, expiry and credit are not revenue;
* **collected**: cash that came in: top-ups, the price of credit packs paid outside, minus
  refunds. Free credit (included credit, goodwill, a bonus) is never collected;
* **adjustments**: the signed sum of corrections, in their own column;
* **AI cost**: what the operator's key spent on this customer (`usage_daily`, payer `operator`),
  converted at today's fixed rate and labelled an estimate, with the count of calls that had no
  known price (D4);
* **margin**: billed minus AI cost.

The operator's own workspace and the COB system workspace are left out; customers on the
Internal plan are listed with billed 0. Everything is shown in one chosen currency, each account
converted at today's fixed rate (display only: spec section 3).
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import time as dtime  # P1 payments
from typing import Any, Final

from sqlalchemy import String, case, cast, func, literal, select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.billing_statements import PnlOut, PnlRow, PnlTotals
from callobuzz_rag.billing.ledger import EntryType
from callobuzz_rag.billing.money import PPM, Currency, fx_ppm, usd_micro_to_micro
from callobuzz_rag.billing.statements import month_range, next_month
from callobuzz_rag.db.models.add_on import AddOnGrant
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.payment import Payment  # P1 payments
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.usage import UsageDaily
from callobuzz_rag.db.models.wallet_ledger import WalletLedger

MAX_MONTHS: Final = 24
MICRO_PER_MINOR: Final = 10_000


@dataclass(frozen=True)
class LedgerGroup:
    """Ledger rows of one account that share a type and a direction: their sum, and (for a
    credit pack) the cash part of it."""

    account_id: uuid.UUID
    entry_type: str
    positive: bool
    amount_minor: int
    cash_minor: int = 0  # add-on packs paid outside: the price, never the bonus on top


@dataclass(frozen=True)
class Customer:
    tenant_id: uuid.UUID
    name: str
    currency: Currency  # the account's
    exempt: bool  # the Internal plan: never charged
    billed_minor: int = 0
    collected_minor: int = 0
    adjustments_minor: int = 0
    ai_cost_usd_micro: int = 0
    unpriced_calls: int = 0
    # P1 payments: what the payment gateway charged (its fee without GST, and that GST,
    # which is input credit shown apart), in the account's currency
    gateway_fee_minor: int = 0
    gateway_fee_tax_minor: int = 0
    # /P1 payments


def fold_groups(groups: Iterable[LedgerGroup]) -> dict[uuid.UUID, tuple[int, int, int]]:
    """`{account: (billed, collected, adjustments)}` in the account's minor units."""
    billed: dict[uuid.UUID, int] = defaultdict(int)
    collected: dict[uuid.UUID, int] = defaultdict(int)
    adjustments: dict[uuid.UUID, int] = defaultdict(int)
    for g in groups:
        kind = g.entry_type
        if kind in (EntryType.SUBSCRIPTION_CHARGE.value, EntryType.USAGE_CHARGE.value):
            billed[g.account_id] -= g.amount_minor
        elif kind == EntryType.ADD_ON_PURCHASE.value:
            if g.positive:
                collected[g.account_id] += g.cash_minor
            else:
                billed[g.account_id] -= g.amount_minor
        elif kind == EntryType.TOP_UP.value:
            collected[g.account_id] += g.amount_minor
        elif kind == EntryType.REFUND.value:
            collected[g.account_id] += g.amount_minor  # stored negative: money going out
        elif kind == EntryType.ADJUSTMENT.value:
            adjustments[g.account_id] += g.amount_minor
        # included credit and expiry are neither revenue nor cash
    ids = set(billed) | set(collected) | set(adjustments)
    return {i: (billed[i], collected[i], adjustments[i]) for i in ids}


def convert_minor(minor: int, source: Currency, target: Currency, rate: float) -> int:
    """`minor` units of `source` as `target` at the fixed USD/INR rate, rounded half away from
    zero. Integer math on the rate in parts per million."""
    if source == target or minor == 0:
        return minor
    fx = fx_ppm(rate, "INR")
    sign = -1 if minor < 0 else 1
    size = abs(minor)
    if source == "INR":  # rupees to dollars
        return sign * ((size * PPM + fx // 2) // fx)
    return sign * ((size * fx + PPM // 2) // PPM)  # dollars to rupees


def cost_minor(usd_micro: int, currency: Currency, rate: float) -> int:
    """The operator's USD cost in `currency` minor units, rounded half up."""
    micro = usd_micro_to_micro(usd_micro, fx_ppm(rate, currency))
    return (micro + MICRO_PER_MINOR // 2) // MICRO_PER_MINOR


def margin_pct(margin: int, billed: int) -> float | None:
    return round(margin * 100 / billed, 1) if billed > 0 else None


def build_rows(
    customers: Sequence[Customer], *, currency: Currency, rate: float
) -> tuple[list[PnlRow], PnlTotals]:
    """One row per customer and the totals, all in `currency`."""
    rows: list[PnlRow] = []
    for c in customers:
        billed = convert_minor(c.billed_minor, c.currency, currency, rate)
        collected = convert_minor(c.collected_minor, c.currency, currency, rate)
        adjustments = convert_minor(c.adjustments_minor, c.currency, currency, rate)
        cost = cost_minor(c.ai_cost_usd_micro, currency, rate)
        fee = convert_minor(c.gateway_fee_minor, c.currency, currency, rate)  # P1 payments
        fee_tax = convert_minor(c.gateway_fee_tax_minor, c.currency, currency, rate)  # P1
        margin = billed - cost - fee  # P1 payments: the gateway's fee is a cost too
        rows.append(
            PnlRow(
                tenant_id=c.tenant_id,
                name=c.name,
                billed_minor=billed,
                collected_minor=collected,
                adjustments_minor=adjustments,
                ai_cost_minor=cost,
                ai_cost_known=c.unpriced_calls == 0,
                unpriced_calls=c.unpriced_calls,
                gateway_fee_minor=fee,  # P1 payments
                gateway_fee_tax_minor=fee_tax,  # P1 payments
                margin_minor=margin,
                margin_pct=margin_pct(margin, billed),
                exempt=c.exempt,
            )
        )
    rows.sort(key=lambda r: (-r.margin_minor, r.name.lower()))
    billed_total = sum(r.billed_minor for r in rows)
    cost_total = sum(r.ai_cost_minor for r in rows)
    fee_total = sum(r.gateway_fee_minor for r in rows)  # P1 payments
    margin_total = billed_total - cost_total - fee_total
    totals = PnlTotals(
        billed_minor=billed_total,
        collected_minor=sum(r.collected_minor for r in rows),
        adjustments_minor=sum(r.adjustments_minor for r in rows),
        ai_cost_minor=cost_total,
        ai_cost_known=all(r.ai_cost_known for r in rows),
        unpriced_calls=sum(r.unpriced_calls for r in rows),
        gateway_fee_minor=fee_total,  # P1 payments
        gateway_fee_tax_minor=sum(r.gateway_fee_tax_minor for r in rows),  # P1 payments
        margin_minor=margin_total,
        margin_pct=margin_pct(margin_total, billed_total),
    )
    return rows, totals


def parse_range(start: str | None, end: str | None, today: date) -> tuple[date, date]:
    """The first-of-month bounds of the range (`YYYY-MM` each; both default to this month).

    Raises:
        APIError: 422 `invalid_range` (start after end), `range_too_long` (over 24 months).
    """

    def month(text: str | None, default: date) -> date:
        if text is None:
            return default
        year, mon = text.split("-")
        return date(int(year), int(mon), 1)

    current = today.replace(day=1)
    last = month(end, current)
    first = month(start, last if end is not None else current)
    if first > last:
        raise APIError(422, "The start month must be on or before the end month.", "invalid_range")
    if len(month_range(first, last)) > MAX_MONTHS:
        raise APIError(
            422, f"A P&L covers at most {MAX_MONTHS} months at a time.", "range_too_long"
        )
    return first, last


# ------------------------------------------------------------------ the database side


async def gather(session: Any, first: date, last: date) -> list[Customer]:
    """Every customer with money movements or operator-paid usage in the months
    `first..last` (inclusive), the operator's own and the COB system workspace left out."""
    pack_cash = case(
        (
            (WalletLedger.entry_type == EntryType.ADD_ON_PURCHASE.value)
            & (WalletLedger.amount_minor > 0)
            & AddOnGrant.payment.in_(("external", "gateway")),  # P1 payments: paid online is cash
            func.least(WalletLedger.amount_minor, AddOnGrant.price_minor),
        ),
        else_=literal(0),
    )
    positive = WalletLedger.amount_minor > 0
    ledger_rows = (
        await session.execute(
            select(
                WalletLedger.account_id,
                WalletLedger.entry_type,
                positive.label("positive"),
                func.coalesce(func.sum(WalletLedger.amount_minor), 0),
                func.coalesce(func.sum(pack_cash), 0),
            )
            .select_from(WalletLedger)
            .outerjoin(
                AddOnGrant,
                (WalletLedger.reference_type == "add_on_grant")
                & (cast(AddOnGrant.id, String) == WalletLedger.reference_id),
            )
            .where(WalletLedger.period >= first, WalletLedger.period <= last)
            .group_by(WalletLedger.account_id, WalletLedger.entry_type, positive)
        )
    ).all()
    figures = fold_groups(
        LedgerGroup(r[0], r[1], bool(r[2]), int(r[3]), int(r[4])) for r in ledger_rows
    )
    accounts: dict[uuid.UUID, BillingAccount] = {}
    if figures:
        accounts = {
            a.id: a
            for a in (
                await session.execute(
                    select(BillingAccount).where(BillingAccount.id.in_(list(figures)))
                )
            )
            .scalars()
            .all()
        }
    usage = {
        r[0]: (int(r[1]), int(r[2]))
        for r in (
            await session.execute(
                select(
                    UsageDaily.tenant_id,
                    func.coalesce(func.sum(UsageDaily.cost_micro), 0),
                    func.coalesce(func.sum(UsageDaily.unpriced_requests), 0),
                )
                .where(
                    UsageDaily.payer == "operator",
                    UsageDaily.tenant_id.is_not(None),
                    UsageDaily.day >= first,
                    UsageDaily.day < next_month(last),
                )
                .group_by(UsageDaily.tenant_id)
            )
        ).all()
    }
    # P1 payments: the gateway's fees by workspace, for payments captured in the range
    fees = {
        r[0]: (int(r[1]), int(r[2]))
        for r in (
            await session.execute(
                select(
                    Payment.tenant_id,
                    func.coalesce(
                        func.sum(Payment.fee_minor - func.coalesce(Payment.fee_tax_minor, 0)), 0
                    ),
                    func.coalesce(func.sum(Payment.fee_tax_minor), 0),
                )
                .where(
                    Payment.fee_minor.is_not(None),
                    Payment.captured_at >= datetime.combine(first, dtime.min, tzinfo=UTC),
                    Payment.captured_at < datetime.combine(next_month(last), dtime.min, tzinfo=UTC),
                )
                .group_by(Payment.tenant_id)
            )
        ).all()
    }
    # /P1 payments
    tenant_ids = {accounts[a].tenant_id for a in figures if a in accounts} | set(usage) | set(fees)
    if not tenant_ids:
        return []
    tenants = (
        await session.execute(
            select(Tenant.id, Tenant.name, Tenant.is_operator, Tenant.is_system, Plan.is_internal)
            .join(Plan, Plan.id == Tenant.plan_id)
            .where(Tenant.id.in_(tenant_ids))
        )
    ).all()
    by_tenant = {
        accounts[a].tenant_id: (billed, collected, adjusted)
        for a, (billed, collected, adjusted) in figures.items()
        if a in accounts
    }
    currencies = {a.tenant_id: a.currency for a in accounts.values()}
    out: list[Customer] = []
    for tid, name, is_operator, is_system, is_internal in tenants:
        if is_operator or is_system:
            continue
        billed, collected, adjusted = by_tenant.get(tid, (0, 0, 0))
        cost, unpriced = usage.get(tid, (0, 0))
        fee, fee_tax = fees.get(tid, (0, 0))  # P1 payments
        out.append(
            Customer(
                tenant_id=tid,
                name=name,
                currency=currencies.get(tid, "INR"),  # type: ignore[arg-type]
                exempt=bool(is_internal),
                billed_minor=billed,
                collected_minor=collected,
                adjustments_minor=adjusted,
                ai_cost_usd_micro=cost,
                unpriced_calls=unpriced,
                gateway_fee_minor=fee,  # P1 payments
                gateway_fee_tax_minor=fee_tax,  # P1 payments
            )
        )
    return out


async def build_pnl(
    session: Any, first: date, last: date, *, currency: Currency, rate: float
) -> PnlOut:
    customers = await gather(session, first, last)
    rows, totals = build_rows(customers, currency=currency, rate=rate)
    return PnlOut(
        currency=currency,
        rate=rate,
        start=f"{first:%Y-%m}",
        end=f"{last:%Y-%m}",
        months=[f"{m:%Y-%m}" for m in month_range(first, last)],
        rows=rows,
        totals=totals,
    )
