"""R6, the nightly settlement check (P1): the provider's report for a day against our payments.

Called from `billing/reconcile.py` when a payment provider is configured. It reads the provider's
settlement report for the day once, then

* applies a settled payment we do not have (`apply_captured(source="recon")`, after fetching its
  truth) and reports it (R6a): a lost webhook is healed, and the operator still hears about it;
* copies the fee, the fee's GST, the settlement id and its date onto each payment (idempotent);
* reports amounts that differ (R6c, never auto-fixed), captured payments with no ledger entry
  (R6d), old payments still unsettled after three working days (R6b) and refunds we did not start
  (R6e, informational).

Nothing here is a payment-side effect beyond `apply_captured`; the findings go into the same
`billing_reconciliations` row as R1-R5.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.billing import reconcile
from callobuzz_rag.billing.payments import capture
from callobuzz_rag.billing.payments.base import PaymentProvider, ProviderError
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.payment import Payment, PaymentRefund
from callobuzz_rag.db.models.wallet_ledger import WalletLedger

logger = logging.getLogger(__name__)

SETTLEMENT_DELAY_WORKING_DAYS = 3
CREDITED = ("captured", "partially_refunded", "refunded")


def _fact(p: Payment) -> reconcile.PaymentFact:
    return reconcile.PaymentFact(
        p.id, p.tenant_id, p.account_id, p.provider_payment_id, p.gross_minor
    )


async def settlement_findings(
    session: AsyncSession, provider: PaymentProvider, day: Any, now: datetime
) -> tuple[list[reconcile.Finding], list[capture.CaptureResult]]:
    """R6 findings for `day`, and the captures it applied (for the follow-up mail after the
    caller commits). A provider that cannot be reached yields one informational finding: the
    next night checks again."""
    try:
        lines = await provider.settlement_lines(day)
    except ProviderError as exc:
        logger.warning("Settlement report for %s unavailable (%s)", day, exc.kind)
        return [
            reconcile.Finding(
                "R6f", detail={"day": str(day), "reason": exc.kind}, informational=True
            )
        ], []
    payment_ids = [line.entity_id for line in lines if line.type == "payment"]
    refund_ids = [line.entity_id for line in lines if line.type == "refund"]
    ours: dict[str, Payment] = {}
    if payment_ids:
        ours = {
            p.provider_payment_id: p
            for p in (
                await session.execute(
                    select(Payment).where(Payment.provider_payment_id.in_(payment_ids))
                )
            )
            .scalars()
            .all()
        }
    known_refunds: frozenset[str] = frozenset()
    if refund_ids:
        stored = (
            await session.execute(
                select(PaymentRefund.provider_refund_id).where(
                    PaymentRefund.provider_refund_id.in_(refund_ids)
                )
            )
        ).scalars()
        known_refunds = frozenset(rid for rid in stored if rid is not None)
    facts_unsettled = (
        (
            await session.execute(
                select(Payment).where(
                    Payment.status.in_(CREDITED),
                    Payment.settled_at.is_(None),
                    Payment.settlement_id.is_(None),
                    Payment.captured_at
                    < reconcile.working_days_before(now, SETTLEMENT_DELAY_WORKING_DAYS),
                )
            )
        )
        .scalars()
        .all()
    )
    findings = reconcile.check_settlements(
        reconcile.SettlementFacts(
            lines=lines,
            payments={k: _fact(v) for k, v in ours.items()},
            known_refunds=known_refunds,
            unsettled=[_fact(p) for p in facts_unsettled],
            entry_missing=await _entry_missing(session),
        )
    )
    applied: list[capture.CaptureResult] = []
    healed = await _heal_missing(session, provider, lines, ours, now, applied)
    for finding in findings:
        if finding.check == "R6a" and finding.detail["provider_payment_id"] in healed:
            finding.detail["healed"] = True
    _copy_fees(lines, ours)
    await session.flush()
    return findings, applied


async def _entry_missing(session: AsyncSession) -> list[reconcile.PaymentFact]:
    """Credited payments whose workspace still has an account but whose ledger entry is gone."""
    rows = (
        (
            await session.execute(
                select(Payment)
                .join(BillingAccount, BillingAccount.id == Payment.account_id)
                .outerjoin(WalletLedger, WalletLedger.id == Payment.entry_id)
                .where(Payment.status.in_(CREDITED), WalletLedger.id.is_(None))
            )
        )
        .scalars()
        .all()
    )
    return [_fact(p) for p in rows]


async def _heal_missing(
    session: AsyncSession,
    provider: PaymentProvider,
    lines: list[Any],
    ours: dict[str, Payment],
    now: datetime,
    applied: list[capture.CaptureResult],
) -> set[str]:
    """Apply each settled payment we do not have, from the provider's own record of it."""
    healed: set[str] = set()
    for line in lines:
        if line.type != "payment" or line.entity_id in ours:
            continue
        try:
            info = await provider.fetch_payment(line.entity_id)
        except ProviderError:
            continue
        async with session.begin_nested():
            result = await capture.apply_captured(session, info, source="recon", now=now)
        applied.append(result)
        if result.created:
            healed.add(line.entity_id)
            row = (
                await session.execute(
                    select(Payment).where(Payment.provider_payment_id == line.entity_id)
                )
            ).scalar_one_or_none()
            if row is not None:
                ours[line.entity_id] = row
    return healed


def _copy_fees(lines: list[Any], ours: dict[str, Payment]) -> None:
    """The fee, its GST, the settlement and its date from each line onto our row (only when they
    differ, so the nightly pass is idempotent)."""
    for line in lines:
        if line.type != "payment":
            continue
        payment = ours.get(line.entity_id)
        if payment is None:
            continue
        if payment.fee_minor != line.fee_minor:
            payment.fee_minor = line.fee_minor
        if payment.fee_tax_minor != line.tax_minor:
            payment.fee_tax_minor = line.tax_minor
        if line.settlement_id and payment.settlement_id != line.settlement_id:
            payment.settlement_id = line.settlement_id
        if line.settled_at and payment.settled_at != line.settled_at:
            payment.settled_at = line.settled_at
