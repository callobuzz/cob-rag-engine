"""The super admin's transactions for one customer (BIL3): list them, record a top-up, a
correction or a refund by hand.

`GET/POST /api/v1/admin/billing/accounts/{tenant_id}/ledger`. Admin only; every route answers
404 `billing_disabled` while `RAG_BILLING_ENABLED=false`. A manual entry is audited
(`billing.ledger_entry`) in the same transaction as the ledger row; the minute posting,
subscriptions and expiry are recorded in the ledger only (decision D7). The rules live in
`billing/ledger.py`.
"""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.settings_store import read_engine_settings_row
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.billing_ledger import (
    EntryKind,
    LedgerEntryOut,
    LedgerListResponse,
    ManualEntryRequest,
    ReconciliationFinding,
    ReconciliationListResponse,
    ReconciliationOut,
    ReconciliationQueued,
)
from callobuzz_rag.billing import accounts, hooks, ledger, lots
from callobuzz_rag.billing.config import require_billing_enabled
from callobuzz_rag.billing.pricing import TenantNotFoundError
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.billing_reconciliation import BillingReconciliation
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.wallet_ledger import WalletLedger

router = APIRouter(
    prefix="/api/v1/admin/billing",
    tags=["billing"],
    dependencies=[Depends(require_admin), Depends(require_billing_enabled)],
)

_KEY_PREFIX = {"top_up": "topup", "adjustment": "adj", "refund": "refund"}


def entry_out(row: WalletLedger) -> LedgerEntryOut:
    return LedgerEntryOut(
        id=row.id,
        seq=row.seq,
        entry_type=row.entry_type,
        amount_minor=row.amount_minor,
        currency=row.currency,
        balance_after_minor=row.balance_after_minor,
        memo=row.memo,
        memo_public=row.memo_public,
        reference_type=row.reference_type,
        reference_id=row.reference_id,
        idempotency_key=row.idempotency_key,
        period=row.period,
        actor_label=row.actor_label,
        created_at=row.created_at,
    )


def _tenant_not_found() -> APIError:
    return APIError(404, "That workspace does not exist.", "tenant_not_found")


@router.get("/accounts/{tenant_id}/ledger", response_model=LedgerListResponse)
async def list_ledger(
    tenant_id: UUID,
    entry_type: EntryKind | None = Query(None, description="Only this kind of transaction"),
    start: date | None = Query(None, description="First day (UTC), inclusive"),
    end: date | None = Query(None, description="Last day (UTC), inclusive"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> LedgerListResponse:
    """A customer's transactions, newest first, with their balance and what they can still use
    (the balance minus usage the minute posting has not written yet). A customer with no
    account yet has none."""
    if start is not None and end is not None and start > end:
        raise APIError(422, "start must be on or before end", "invalid_range")
    if await session.get(Tenant, tenant_id) is None:
        raise _tenant_not_found()
    account = (
        await session.execute(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if account is None:
        return LedgerListResponse(items=[], total=0, balance_minor=0, available_minor=0)
    where = [WalletLedger.account_id == account.id]
    if entry_type is not None:
        where.append(WalletLedger.entry_type == entry_type)
    if start is not None:
        where.append(WalletLedger.created_at >= datetime.combine(start, time.min, tzinfo=UTC))
    if end is not None:
        where.append(
            WalletLedger.created_at
            < datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
        )
    total = (
        await session.execute(select(func.count()).select_from(WalletLedger).where(*where))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(WalletLedger)
                .where(*where)
                .order_by(WalletLedger.seq.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return LedgerListResponse(
        items=[entry_out(r) for r in rows],
        total=int(total),
        balance_minor=account.balance_minor,
        available_minor=await ledger.available_minor(session, account),
    )


@router.post("/accounts/{tenant_id}/ledger", response_model=LedgerEntryOut, status_code=201)
async def add_ledger_entry(
    tenant_id: UUID,
    body: ManualEntryRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LedgerEntryOut:
    """Record a top-up, an adjustment (signed) or a refund. Amounts are minor units of the
    customer's currency. At or above the confirmation limit the amount must be sent again as
    `confirm_amount_minor` (422 `confirm_mismatch`). A refund cannot be larger than the paid
    balance still available (409 `refund_exceeds_balance`: credit from the plan or goodwill is
    not refundable). Sending the same `idempotency_key` again returns the same transaction
    (200); with another type or amount it is a 409 `idempotency_conflict`. The operator's own
    workspace takes none (409 `billing_exempt`)."""
    try:
        await accounts.account_for_update(session, tenant_id)
        ctx = await accounts.load_context(session, tenant_id)
    except TenantNotFoundError as exc:
        raise _tenant_not_found() from exc
    if ctx.effective.exempt:
        raise APIError(
            409,
            "This is your own workspace (or an internal one): it is never billed, so it has no "
            "transactions.",
            "billing_exempt",
        )
    account, currency = ctx.account, ctx.effective.currency
    key = f"{_KEY_PREFIX[body.entry_type]}:{body.idempotency_key}"
    kind = ledger.EntryType(body.entry_type)
    amount = -body.amount_minor if kind is ledger.EntryType.REFUND else body.amount_minor
    # A retry of a request that already went through replays (or conflicts) in append_entries:
    # the confirmation and refund checks only judge a new request, never its own repeat.
    existing = (
        await session.execute(
            select(WalletLedger).where(
                WalletLedger.account_id == account.id, WalletLedger.idempotency_key == key
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        limit = (await read_engine_settings_row(session))[0].billing.confirm_above_minor.get(
            currency
        )
        if (
            limit is not None
            and abs(body.amount_minor) >= limit
            and (body.confirm_amount_minor != body.amount_minor)
        ):
            raise APIError(
                422,
                "This is a large amount. Type the amount again to confirm it.",
                "confirm_mismatch",
                limit_minor=limit,
                currency=currency,
            )
        if account.ledger_seq == 0 and account.currency != currency:
            account.currency = currency  # until the first entry the plan's currency decides
        if kind is ledger.EntryType.REFUND:
            refundable = min(
                await ledger.available_minor(session, account)
                - await lots.live_credit(session, account.id),
                await ledger.paid_cash_minor(session, account.id),
            )
            if body.amount_minor > max(refundable, 0):
                raise APIError(
                    409,
                    "That is more than the paid balance this customer can still be refunded.",
                    "refund_exceeds_balance",
                    refundable_minor=max(refundable, 0),
                    currency=currency,
                )
    entry = ledger.NewEntry(
        kind,
        amount,
        key,
        reference_type="admin",
        memo=body.memo.strip() if body.memo else None,
        memo_public=body.memo_public.strip() if body.memo_public else None,
        expires_at=body.expires_at,
    )
    [result] = await ledger.append_entries(
        session, account.id, [entry], actor=actor_from_request(request)
    )
    if result.created:
        await record_audit(
            session,
            request,
            "billing.ledger_entry",
            tenant_id=ctx.tenant.id,
            tenant_label=ctx.tenant.name,
            target_type="billing_account",
            target_id=str(account.id),
            details={
                "entry_id": str(result.entry_id),
                "entry_type": body.entry_type,
                "amount_minor": amount,
                "currency": currency,
                "balance_after_minor": result.balance_after_minor,
                "idempotency_key": entry.idempotency_key,
            },
        )
    row = (
        await session.execute(select(WalletLedger).where(WalletLedger.id == result.entry_id))
    ).scalar_one()
    out = entry_out(row)
    await session.commit()
    if result.created:
        await _balance_changed(request, account.id)
    else:
        response.status_code = 200  # a replay of the same request
    return out


async def _balance_changed(request: Request, account_id: UUID) -> None:
    """After the commit: tell the enforcement hook (a top-up resumes a paused workspace)."""
    factory = getattr(request.app.state, "session_factory", None)
    try:
        await hooks.after_balance_change(factory, [account_id])
    except Exception:  # never fail a committed money entry because of a notification
        import logging

        logging.getLogger(__name__).warning(
            "Balance hook failed after a manual entry", exc_info=True
        )


# ---------------------------------------------------------------- the daily reconciliation


def _reconciliation_out(row: BillingReconciliation) -> ReconciliationOut:
    return ReconciliationOut(
        id=row.id,
        ran_at=row.ran_at,
        day=row.day,
        ok=row.ok,
        checked_accounts=row.checked_accounts,
        findings=[ReconciliationFinding(**f) for f in row.findings],
        duration_ms=row.duration_ms,
    )


@router.get("/reconciliation", response_model=ReconciliationListResponse)
async def list_reconciliations(
    limit: int = Query(30, ge=1, le=30),
    session: AsyncSession = Depends(get_session),
) -> ReconciliationListResponse:
    """The last daily checks of the books, newest first: whether every balance still equals
    its transactions, every charge was posted once, and every answer has a charge."""
    rows = (
        (
            await session.execute(
                select(BillingReconciliation)
                .order_by(BillingReconciliation.ran_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return ReconciliationListResponse(items=[_reconciliation_out(r) for r in rows])


@router.post("/reconciliation", response_model=ReconciliationQueued, status_code=202)
async def run_reconciliation() -> ReconciliationQueued:
    """Run the check now instead of waiting for tonight's. It runs in the background; read the
    result with `GET`."""
    from callobuzz_rag.worker.celery_app import celery_app

    try:
        await asyncio.to_thread(
            celery_app.send_task, "callobuzz_rag.reconcile_billing", queue="periodic"
        )
    except Exception as exc:  # an unreachable broker raises kombu/OS errors of many kinds
        raise APIError(
            503, "The task queue is not reachable. Try again in a minute.", "queue_unavailable"
        ) from exc
    return ReconciliationQueued()
