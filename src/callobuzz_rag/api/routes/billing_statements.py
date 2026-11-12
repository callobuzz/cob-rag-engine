"""Monthly statements (BIL6): the super admin reads any customer's, an owner reads their own.

`GET .../statements` lists the months; `GET .../statements/{YYYY-MM}` returns one as JSON, a CSV
download or a standalone printable HTML page (`?format=`; "Download PDF" prints that page, so the
engine carries no PDF library). The current month is built live and marked "so far"; closed
months are the frozen snapshots. Members never see a statement (owner-only), and a statement
never carries the markup or what the operator's AI cost. Every route answers 404
`billing_disabled` while `RAG_BILLING_ENABLED=false`.
"""

import logging
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.accounts.settings_store import read_engine_settings_row
from callobuzz_rag.api.deps import get_session, require_admin, require_owner, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.billing_statements import (
    PnlOut,
    StatementFormat,
    StatementListResponse,
    StatementOut,
    StatementSummary,
    TopUpRequest,
    TopUpRequestOut,
)
from callobuzz_rag.billing import accounts, pnl, statements
from callobuzz_rag.billing import summary as billing_summary
from callobuzz_rag.billing.config import require_billing_enabled
from callobuzz_rag.billing.money import Currency, Money
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
HTML_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:"

admin_router = APIRouter(
    prefix="/api/v1/admin/billing",
    tags=["billing"],
    dependencies=[Depends(require_admin), Depends(require_billing_enabled)],
)
router = APIRouter(
    prefix="/api/v1/billing", tags=["billing"], dependencies=[Depends(require_billing_enabled)]
)

_FORMATS: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The statement as JSON, a CSV download or a printable HTML page.",
        "content": {"text/csv": {}, "text/html": {}},
    }
}


def _parse_month(text: str) -> date:
    year, month = text.split("-")
    return date(int(year), int(month), 1)


async def _account(session: AsyncSession, tenant_id: UUID) -> BillingAccount | None:
    return (
        await session.execute(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id))
    ).scalar_one_or_none()


async def _list(session: AsyncSession, tenant_id: UUID) -> StatementListResponse:
    account = await _account(session, tenant_id)
    if account is None:
        return StatementListResponse(items=[])
    rows = await statements.summaries(session, account, now=datetime.now(UTC))
    return StatementListResponse(
        items=[
            StatementSummary(
                month=f"{r.month:%Y-%m}",
                currency=account.currency,
                opening_minor=r.opening_minor,
                closing_minor=r.closing_minor,
                live=r.live,
            )
            for r in rows
        ]
    )


async def _statement(
    session: AsyncSession, tenant_id: UUID, month: str, fmt: StatementFormat
) -> Any:
    account = await _account(session, tenant_id)
    if account is None:
        raise statements.not_found()
    data = await statements.statement_for(
        session, account, _parse_month(month), now=datetime.now(UTC)
    )
    tenant = await session.get(Tenant, tenant_id)
    workspace = tenant.name if tenant is not None else "Workspace"
    if fmt == "csv":
        slug = statements.workspace_slug(workspace)
        return Response(
            statements.render_csv(data, workspace),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="statement-{slug}-{month}.csv"',
                "Cache-Control": "no-store",
            },
        )
    if fmt == "html":
        branding = (await read_engine_settings_row(session))[0].branding
        return Response(
            statements.render_html(data, workspace, branding),
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": HTML_CSP,
                "X-Content-Type-Options": "nosniff",
            },
        )
    return StatementOut(**data.model_dump(), workspace=workspace)


# ------------------------------------------------------------------ the super admin


@admin_router.get("/accounts/{tenant_id}/statements", response_model=StatementListResponse)
async def list_customer_statements(
    tenant_id: UUID, session: AsyncSession = Depends(get_session)
) -> StatementListResponse:
    """A customer's statements, newest month first (the current month is marked live). A
    customer with no billing account yet has none."""
    if await session.get(Tenant, tenant_id) is None:
        raise APIError(404, "That workspace does not exist.", "tenant_not_found")
    return await _list(session, tenant_id)


@admin_router.get(
    "/accounts/{tenant_id}/statements/{month}",
    response_model=StatementOut,
    responses=_FORMATS,
)
async def get_customer_statement(
    tenant_id: UUID,
    month: str = Path(..., pattern=MONTH_PATTERN, description="`YYYY-MM` (UTC calendar month)"),
    format: StatementFormat = Query("json", description="json, csv (download) or html (print)"),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """One month of a customer's statement. 404 `statement_not_found` for a month before the
    account existed or in the future."""
    if await session.get(Tenant, tenant_id) is None:
        raise APIError(404, "That workspace does not exist.", "tenant_not_found")
    return await _statement(session, tenant_id, month, format)


# ------------------------------------------------------------------ the owner


def owner_workspace(request: Request) -> UUID:
    """The workspace of an owner session or a `tk_` key; everyone else is refused."""
    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        raise APIError(
            403, "Billing belongs to a workspace, not to one knowledge base.", "scope_forbidden"
        )
    if scope.is_admin or scope.tenant_id is None:
        raise APIError(
            403, "There is no workspace here: use the admin billing pages.", "no_workspace"
        )
    require_owner(request)
    return scope.tenant_id


@router.get("/statements", response_model=StatementListResponse)
async def list_my_statements(
    request: Request, session: AsyncSession = Depends(get_session)
) -> StatementListResponse:
    """This workspace's monthly statements, newest first. Owners only."""
    return await _list(session, owner_workspace(request))


@router.get("/statements/{month}", response_model=StatementOut, responses=_FORMATS)
async def get_my_statement(
    request: Request,
    month: str = Path(..., pattern=MONTH_PATTERN, description="`YYYY-MM` (UTC calendar month)"),
    format: StatementFormat = Query("json", description="json, csv (download) or html (print)"),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """One month of this workspace's statement: opening balance, subscription, usage by
    knowledge base, model and source, add-ons, top-ups and the closing balance. Owners only."""
    return await _statement(session, owner_workspace(request), month, format)


# ------------------------------------------------------------------ the platform P&L


@admin_router.get("/pnl", response_model=PnlOut)
async def get_pnl(
    request: Request,
    start: str | None = Query(None, pattern=MONTH_PATTERN, description="First month, `YYYY-MM`"),
    end: str | None = Query(None, pattern=MONTH_PATTERN, description="Last month, `YYYY-MM`"),
    currency: Currency | None = Query(None, description="Show everything in INR or USD"),
    session: AsyncSession = Depends(get_session),
) -> PnlOut:
    """What each customer was billed and paid against what their answers cost the operator's
    AI key, with the margin. The range defaults to this month and covers at most 24 months;
    every account is converted at today's fixed rate into the chosen currency (default: the
    engine's). The operator's own workspace is left out; AI cost is an estimate."""
    today = datetime.now(UTC).date()
    first, last = pnl.parse_range(start, end, today)
    shown: Currency = (
        currency or (await read_engine_settings_row(session))[0].billing.default_currency
    )
    rate = request.app.state.settings.usd_inr_rate
    return await pnl.build_pnl(session, first, last, currency=shown, rate=rate)


# ------------------------------------------------------------------ "request top-up"


@router.post("/top-up-requests", response_model=TopUpRequestOut, status_code=202)
async def request_top_up(
    request: Request, body: TopUpRequest, session: AsyncSession = Depends(get_session)
) -> TopUpRequestOut:
    """Ask the operator to add money, until a payment gateway exists: an e-mail to the super
    admin with the amount and a note, and an audit row. One request per workspace per hour,
    judged from the last audit row (nothing that can fail closed). Owners only."""
    from callobuzz_rag.accounts.email.dispatch import enqueue_email
    from callobuzz_rag.accounts.email.render import dashboard_link

    tenant_id = owner_workspace(request)
    now = datetime.now(UTC)
    # one request at a time per workspace, so a double-click cannot slip two past the check
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"topup:{tenant_id}"}
    )
    last = await billing_summary.last_top_up_request(session, tenant_id, now)
    if last is not None:
        wait = billing_summary.retry_after_seconds(last, now)
        raise APIError(
            429,
            "You asked a moment ago. Please wait before asking again; the team has your request.",
            "topup_request_too_soon",
            headers={"Retry-After": str(wait)},
            retry_after_s=wait,
        )
    ctx = await accounts.load_context(session, tenant_id)
    if ctx.effective.exempt:
        raise APIError(
            409, "This workspace is never charged, so there is nothing to top up.", "billing_exempt"
        )
    currency = ctx.effective.currency
    await record_audit(
        session,
        request,
        billing_summary.TOP_UP_ACTION,
        tenant_id=tenant_id,
        tenant_label=ctx.tenant.name,
        target_type="billing_account",
        target_id=str(ctx.account.id),
        details={"amount_minor": body.amount_minor, "currency": currency},
    )
    await session.commit()
    settings = request.app.state.settings
    operator = (settings.superadmin_email or "").strip()
    if not operator:
        logger.warning("Top-up request from %s has no operator e-mail to go to", ctx.tenant.name)
        return TopUpRequestOut(sent=False)
    user = getattr(request.state, "user", None)
    context = {
        "tenant_name": ctx.tenant.name,
        "owner_name": str(user.name) if user is not None else "someone using the workspace key",
        "owner_email": str(user.email) if user is not None else "",
        "amount": Money(body.amount_minor, currency).format() if body.amount_minor else "",
        "note": (body.note or "").strip(),
        "account_url": dashboard_link(
            settings.dashboard_url, f"/admin/customers/{tenant_id}?tab=billing"
        ),
    }
    try:
        enqueue_email("billing_top_up_request", operator, context, expires_s=24 * 3600)
    except Exception as exc:  # the request is audited; the operator can still see it there
        logger.error("Top-up request mail not queued (%s)", type(exc).__name__)
        return TopUpRequestOut(sent=False)
    return TopUpRequestOut(sent=True)
