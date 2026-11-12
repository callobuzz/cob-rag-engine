"""The daily billing reconciliation: does every number still add up?

Five checks. The per-account ones (R1, R2, R4, R5) work on a handful of facts the database
gathers with aggregates (`AccountFacts`), so the rules are pure and testable; R3 compares the
day's usage with the day's charges.

* R1  ledger integrity: `seq` runs 1..n, each `balance_after` is the running sum, and the
      cached balance and sequence on the account equal the ledger (the finding names the first
      bad `seq`).
* R2  conservation: every charge ever made is posted, carried as a remainder, or still pending
      (invariant I6).
* R3  coverage: for a customer billed for a payer all day, the day's `usage_daily` requests and
      cost equal the day's `usage_charges` questions and cost. A pricing change during the day
      (`pricing_changed`) or a knowledge base deleted since (`knowledge_base_deleted`) explain a
      difference and are informational.
* R4  stale charges: unposted for more than ten minutes (the posting job is not running).
* R5  credit lots holding more than the balance can cover (informational).
* R6  settlements (P1, only with a payment provider): every payment the provider settled is
      one of ours (a missing one is applied, then reported), amounts agree, every captured
      payment has its ledger entry, an old payment is not still unsettled; fees are copied
      onto the payment.
* R7  tax documents (P2): every captured payment has exactly one invoice or bill of supply;
      every processed refund has a credit note or says why not (`credit_note_skipped`). Never
      auto-fixed: a document is issued only in the capture or refund transaction.

A run with no finding that is not informational is `ok`.
"""

import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import text

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.billing.config import billing_enabled
from callobuzz_rag.billing.money import MICRO_PER_MINOR
from callobuzz_rag.db.models.billing_reconciliation import BillingReconciliation

logger = logging.getLogger(__name__)

MAX_FINDINGS: Final = 500
STALE_AFTER: Final = timedelta(minutes=10)


@dataclass(frozen=True)
class Finding:
    check: str
    account_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    informational: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "account_id": str(self.account_id) if self.account_id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "detail": self.detail,
            "informational": self.informational,
        }


@dataclass(frozen=True)
class AccountFacts:
    """What the database says about one account, boiled down."""

    account_id: uuid.UUID
    tenant_id: uuid.UUID
    cached_balance: int
    cached_seq: int
    residue_micro: int
    ledger_count: int  # rows in the ledger
    ledger_sum: int  # sum of their amounts
    first_bad_seq: int | None  # first seq whose number or running sum is wrong
    posted_usage_minor: int  # sum of usage_charge amounts, negative
    charged_micro: int  # every charge ever made
    unposted_micro: int
    stale_unposted: int  # unposted charges older than the grace
    live_lot_minor: int  # credit still in lots


def check_account(f: AccountFacts) -> list[Finding]:
    """R1, R2, R4 and R5 for one account."""
    out: list[Finding] = []

    def finding(check: str, informational: bool = False, **detail: Any) -> None:
        out.append(Finding(check, f.account_id, f.tenant_id, detail, informational))

    if f.first_bad_seq is not None:
        finding("ledger_integrity", first_bad_seq=f.first_bad_seq)
    elif f.cached_balance != f.ledger_sum or f.cached_seq != f.ledger_count:
        finding(
            "ledger_integrity",
            cached_balance=f.cached_balance,
            ledger_sum=f.ledger_sum,
            cached_seq=f.cached_seq,
            ledger_count=f.ledger_count,
        )
    expected = -f.posted_usage_minor * MICRO_PER_MINOR + f.residue_micro + f.unposted_micro
    if f.charged_micro != expected:
        finding("conservation", charged_micro=f.charged_micro, accounted_micro=expected)
    if f.stale_unposted:
        finding("stale_unposted", count=f.stale_unposted)
    if f.live_lot_minor > max(f.cached_balance, 0):
        finding(
            "lot_exceeds_balance",
            informational=True,
            lots_minor=f.live_lot_minor,
            balance_minor=f.cached_balance,
        )
    return out


@dataclass(frozen=True)
class DayFacts:
    """One (customer, payer)'s day, from usage and from charges."""

    tenant_id: uuid.UUID
    payer: str
    usage_requests: int
    usage_cost_micro: int
    charge_questions: int
    charge_cost_micro: int
    pricing_changed: bool = False  # a price or term changed during the day
    deleted_kb_charges: int = 0  # charges of a knowledge base that no longer exists


def check_day(d: DayFacts) -> list[Finding]:
    """R3 for one customer and payer on one day (only customers billed for that payer)."""
    matched = d.usage_requests == d.charge_questions and d.usage_cost_micro == d.charge_cost_micro
    if d.pricing_changed and not matched:
        # a change during the day explains a difference; a day that adds up needs no excuse
        return [
            Finding(
                "pricing_changed",
                tenant_id=d.tenant_id,
                detail={"payer": d.payer},
                informational=True,
            )
        ]
    if matched:
        if d.deleted_kb_charges:
            # The charges of a knowledge base that was deleted since are left out of the
            # comparison (its usage rows went with it): it matches, but say why they differ.
            return [
                Finding(
                    "knowledge_base_deleted",
                    tenant_id=d.tenant_id,
                    detail={"payer": d.payer, "charges": d.deleted_kb_charges},
                    informational=True,
                )
            ]
        return []
    return [
        Finding(
            "coverage",
            tenant_id=d.tenant_id,
            detail={
                "payer": d.payer,
                "usage_requests": d.usage_requests,
                "charge_questions": d.charge_questions,
                "usage_cost_micro": d.usage_cost_micro,
                "charge_cost_micro": d.charge_cost_micro,
            },
        )
    ]


# P1 payments
@dataclass(frozen=True)
class PaymentFact:
    """One of our payments, boiled down for R6."""

    payment_id: uuid.UUID
    tenant_id: uuid.UUID
    account_id: uuid.UUID
    provider_payment_id: str
    gross_minor: int


@dataclass(frozen=True)
class SettlementFacts:
    """What the provider settled for one day, next to what we hold."""

    lines: Sequence[Any]  # `SettlementLine`s
    payments: dict[str, PaymentFact]  # ours, by provider payment id, for the ids on the lines
    known_refunds: frozenset[str]  # provider refund ids we started
    unsettled: Sequence[PaymentFact]  # captured, older than three working days, never settled
    entry_missing: Sequence[PaymentFact]  # captured, account still there, ledger entry missing


def check_settlements(f: SettlementFacts) -> list[Finding]:
    """R6a-R6e. R6a (a settled payment we do not have) is reported here and healed by the caller;
    R6c (the amounts differ) is never auto-fixed."""
    out: list[Finding] = []
    for line in f.lines:
        if line.type == "payment":
            ours = f.payments.get(line.entity_id)
            if ours is None:
                out.append(
                    Finding(
                        "R6a",
                        detail={
                            "provider_payment_id": line.entity_id,
                            "amount_minor": line.amount_minor,
                            "settlement_id": line.settlement_id,
                        },
                    )
                )
            elif ours.gross_minor != line.amount_minor:
                out.append(
                    Finding(
                        "R6c",
                        account_id=ours.account_id,
                        tenant_id=ours.tenant_id,
                        detail={
                            "provider_payment_id": line.entity_id,
                            "recorded_minor": ours.gross_minor,
                            "settled_minor": line.amount_minor,
                        },
                    )
                )
        elif line.type == "refund" and line.entity_id not in f.known_refunds:
            out.append(
                Finding(
                    "R6e",
                    detail={
                        "provider_refund_id": line.entity_id,
                        "amount_minor": line.amount_minor,
                    },
                    informational=True,  # a refund made in the provider's own dashboard
                )
            )
    out.extend(
        Finding(
            "R6b",
            account_id=p.account_id,
            tenant_id=p.tenant_id,
            detail={"provider_payment_id": p.provider_payment_id, "reason": "settlement_missing"},
        )
        for p in f.unsettled
    )
    out.extend(
        Finding(
            "R6d",
            account_id=p.account_id,
            tenant_id=p.tenant_id,
            detail={"provider_payment_id": p.provider_payment_id, "reason": "ledger_entry_missing"},
        )
        for p in f.entry_missing
    )
    return out


def working_days_before(moment: datetime, days: int) -> datetime:
    """`moment` moved back by `days` working days (Saturday and Sunday do not count)."""
    out = moment
    remaining = days
    while remaining > 0:
        out -= timedelta(days=1)
        if out.weekday() < 5:
            remaining -= 1
    return out


# /P1 payments


# P2 invoices
@dataclass(frozen=True)
class DocumentFact:
    """A payment or refund whose tax document is missing, for R7."""

    tenant_id: uuid.UUID
    account_id: uuid.UUID
    payment_id: uuid.UUID
    refund_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DocumentFacts:
    payments_without_invoice: Sequence[DocumentFact]
    refunds_without_credit_note: Sequence[DocumentFact]


def check_documents(f: DocumentFacts) -> list[Finding]:
    """R7a: a captured payment has no invoice. R7b: a processed refund has neither a credit note
    nor a recorded reason. Both are reported, never fixed here."""
    out = [
        Finding(
            "R7a",
            account_id=p.account_id,
            tenant_id=p.tenant_id,
            detail={"payment_id": str(p.payment_id), "reason": "invoice_missing"},
        )
        for p in f.payments_without_invoice
    ]
    out.extend(
        Finding(
            "R7b",
            account_id=r.account_id,
            tenant_id=r.tenant_id,
            detail={
                "payment_id": str(r.payment_id),
                "refund_id": str(r.refund_id),
                "reason": "credit_note_missing",
            },
        )
        for r in f.refunds_without_credit_note
    )
    return out


_NO_INVOICE = text(
    "SELECT p.tenant_id, p.account_id, p.id FROM payments p"
    " WHERE p.status IN ('captured','partially_refunded','refunded') AND p.created_at < :before"
    " AND NOT EXISTS (SELECT 1 FROM tax_documents d"
    "  WHERE d.payment_id = p.id AND d.doc_type <> 'credit_note')"
    " ORDER BY p.created_at LIMIT 200"
)
_NO_CREDIT_NOTE = text(
    "SELECT r.tenant_id, r.account_id, r.payment_id, r.id FROM payment_refunds r"
    " WHERE r.status = 'processed' AND r.credit_note_skipped IS NULL AND r.created_at < :before"
    " AND NOT EXISTS (SELECT 1 FROM tax_documents d WHERE d.refund_id = r.id)"
    " ORDER BY r.created_at LIMIT 200"
)


async def document_findings(session: Any, now: datetime) -> list[Finding]:
    """R7 from the database: payments and refunds a minute old or more with no document."""
    before = {"before": now - timedelta(minutes=1)}
    no_invoice = (await session.execute(_NO_INVOICE, before)).all()
    no_note = (await session.execute(_NO_CREDIT_NOTE, before)).all()
    return check_documents(
        DocumentFacts(
            payments_without_invoice=[DocumentFact(r[0], r[1], r[2]) for r in no_invoice],
            refunds_without_credit_note=[DocumentFact(r[0], r[1], r[2], r[3]) for r in no_note],
        )
    )


# /P2 invoices


def is_ok(findings: Sequence[Finding]) -> bool:
    return not any(not f.informational for f in findings)


# ---------------------------------------------------------------- the database side

_LEDGER_FACTS = text(
    """
    WITH w AS (
      SELECT account_id, seq, entry_type, amount_minor, balance_after_minor,
             row_number() OVER (PARTITION BY account_id ORDER BY seq) AS rn,
             sum(amount_minor) OVER (PARTITION BY account_id ORDER BY seq) AS run
      FROM wallet_ledger
    ), l AS (
      SELECT account_id,
             count(*) AS n,
             coalesce(sum(amount_minor), 0) AS total,
             min(seq) FILTER (WHERE rn <> seq OR run <> balance_after_minor) AS bad,
             coalesce(sum(amount_minor) FILTER (WHERE entry_type = 'usage_charge'), 0) AS usage
      FROM w GROUP BY account_id
    ), c AS (
      SELECT account_id,
             coalesce(sum(amount_micro), 0) AS charged,
             coalesce(sum(amount_micro) FILTER (WHERE posted_at IS NULL), 0) AS unposted,
             count(*) FILTER (WHERE posted_at IS NULL AND created_at < :stale) AS stale
      FROM usage_charges GROUP BY account_id
    ), k AS (
      SELECT account_id, coalesce(sum(remaining_minor), 0) AS lots
      FROM credit_lots WHERE remaining_minor > 0 GROUP BY account_id
    )
    SELECT a.id, a.tenant_id, a.balance_minor, a.ledger_seq, a.usage_residue_micro,
           coalesce(l.n, 0), coalesce(l.total, 0), l.bad, coalesce(l.usage, 0),
           coalesce(c.charged, 0), coalesce(c.unposted, 0), coalesce(c.stale, 0),
           coalesce(k.lots, 0)
    FROM billing_accounts a
    LEFT JOIN l ON l.account_id = a.id
    LEFT JOIN c ON c.account_id = a.id
    LEFT JOIN k ON k.account_id = a.id
    """
)

_DAY_FACTS = text(
    """
    WITH u AS (
      SELECT tenant_id, payer, sum(requests) AS requests, sum(cost_micro) AS cost
      FROM usage_daily WHERE day = :day GROUP BY tenant_id, payer
    ), c AS (
      SELECT ch.tenant_id, ch.payer,
             sum(ch.questions) FILTER (WHERE k.id IS NOT NULL) AS questions,
             sum(ch.cost_usd_micro) FILTER (WHERE k.id IS NOT NULL) AS cost,
             count(*) FILTER (WHERE k.id IS NULL) AS deleted
      FROM usage_charges ch LEFT JOIN corpora k ON k.id = ch.corpus_id
      WHERE ch.day = :day GROUP BY ch.tenant_id, ch.payer
    )
    SELECT coalesce(u.tenant_id, c.tenant_id), coalesce(u.payer, c.payer),
           coalesce(u.requests, 0), coalesce(u.cost, 0),
           coalesce(c.questions, 0), coalesce(c.cost, 0), coalesce(c.deleted, 0)
    FROM u FULL OUTER JOIN c ON c.tenant_id = u.tenant_id AND c.payer = u.payer
    """
)

_PRICING_CHANGED = text(
    """
    SELECT DISTINCT t.id FROM tenants t
    JOIN plan_pricing p ON p.plan_id = t.plan_id
    WHERE p.created_at >= :start AND p.created_at < :end
    UNION
    SELECT DISTINCT tenant_id FROM audit_log
    WHERE action IN ('billing.account_updated', 'tenant.plan_changed')
      AND created_at >= :start AND created_at < :end AND tenant_id IS NOT NULL
    """
)


@dataclass(frozen=True)
class RunResult:
    run_id: uuid.UUID
    day: date
    ok: bool
    checked_accounts: int
    findings: list[Finding]
    duration_ms: int
    heals: list[Any] = field(default_factory=list)  # P1 payments: payments R6a applied


async def gather_findings(
    session: Any, day: date, now: datetime, *, provider: Any = None, heals: list[Any] | None = None
) -> tuple[int, list[Finding]]:
    """All findings for `day` (R3) and for the books as they stand (R1, R2, R4, R5)."""
    from callobuzz_rag.billing.pricing import effective_pricing

    findings: list[Finding] = []
    rows = (await session.execute(_LEDGER_FACTS, {"stale": now - STALE_AFTER})).all()
    for r in rows:
        findings.extend(
            check_account(
                AccountFacts(
                    account_id=r[0],
                    tenant_id=r[1],
                    cached_balance=int(r[2]),
                    cached_seq=int(r[3]),
                    residue_micro=int(r[4]),
                    ledger_count=int(r[5]),
                    ledger_sum=int(r[6]),
                    first_bad_seq=int(r[7]) if r[7] is not None else None,
                    posted_usage_minor=int(r[8]),
                    charged_micro=int(r[9]),
                    unposted_micro=int(r[10]),
                    stale_unposted=int(r[11]),
                    live_lot_minor=int(r[12]),
                )
            )
        )
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    changed = {
        row[0]
        for row in (
            await session.execute(
                _PRICING_CHANGED, {"start": start, "end": start + timedelta(days=1)}
            )
        ).all()
    }
    for r in (await session.execute(_DAY_FACTS, {"day": day})).all():
        tenant_id, payer = r[0], r[1]
        try:
            eff = await effective_pricing(session, tenant_id)
        except Exception:  # a deleted workspace: nothing to compare
            continue
        if not eff.chargeable(payer):
            continue
        findings.extend(
            check_day(
                DayFacts(
                    tenant_id=tenant_id,
                    payer=payer,
                    usage_requests=int(r[2]),
                    usage_cost_micro=int(r[3]),
                    charge_questions=int(r[4]),
                    charge_cost_micro=int(r[5]),
                    pricing_changed=tenant_id in changed,
                    deleted_kb_charges=int(r[6]),
                )
            )
        )
    # P1 payments: R6, only with a payment provider (`heals` collects what it applied)
    if provider is not None:
        from callobuzz_rag.billing.payments import recon

        found, applied = await recon.settlement_findings(session, provider, day, now)
        findings.extend(found)
        if heals is not None:
            heals.extend(applied)
    # /P1 payments
    findings.extend(await document_findings(session, now))  # P2 invoices
    return len(rows), findings


async def run(
    session_factory: Any,
    *,
    day: date | None = None,
    now: datetime | None = None,
    provider: Any = None,  # P1 payments
) -> RunResult | None:
    """Run every check for `day` (default: yesterday, UTC), store the result and, when
    something is wrong, record `billing.reconciliation_failed` (system actor). None when
    billing is off."""
    if not billing_enabled():
        return None
    moment = now or datetime.now(UTC)
    target = day or (moment.date() - timedelta(days=1))
    started = time.monotonic()
    async with session_factory() as session:
        heals: list[Any] = []  # P1 payments
        checked, findings = await gather_findings(
            session, target, moment, provider=provider, heals=heals
        )
        ok = is_ok(findings)
        row = BillingReconciliation(
            id=uuid.uuid4(),
            ran_at=moment,
            day=target,
            ok=ok,
            checked_accounts=checked,
            findings=[f.as_json() for f in findings[:MAX_FINDINGS]],
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        session.add(row)
        if not ok:
            await record_audit(
                session,
                None,
                "billing.reconciliation_failed",
                target_type="billing_reconciliation",
                target_id=str(row.id),
                details={
                    "run_id": str(row.id),
                    "findings": sum(1 for f in findings if not f.informational),
                },
            )
            logger.error("Billing reconciliation for %s found %d problems", target, len(findings))
        await session.commit()
        return RunResult(
            row.id, target, ok, checked, findings[:MAX_FINDINGS], row.duration_ms, heals
        )
