"""GET /api/v1/admin/costs — what the platform spends on models (super admin only).

Totals for today, this month and a chosen range; the range broken down by customer,
knowledge base, model, source and payer; the OpenRouter balance; and a CSV of every usage row.
Costs are recorded per request from the provider's own figures (see `GET /api/v1/usage`).
"""

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.analytics.consumption import costs_csv, with_labels
from callobuzz_rag.analytics.openrouter_balance import get_openrouter_balance
from callobuzz_rag.analytics.usage import month_start, sum_rows, usage_report
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.provider_balances import provider_summary  # BIL1
from callobuzz_rag.api.routes.usage import OpenRouterBalanceOut, UsageRow
from callobuzz_rag.api.schemas.provider_balances import ProviderSummaryOut  # BIL1

router = APIRouter(
    prefix="/api/v1/admin/costs", tags=["usage"], dependencies=[Depends(require_admin)]
)

_MAX_RANGE_DAYS = 366


class CostsResponse(BaseModel):
    start: date
    end: date
    usd_inr_rate: float
    today: UsageRow
    month: UsageRow = Field(description="From the first of this UTC month to today")
    range: UsageRow = Field(description="start to end")
    by_tenant: list[UsageRow]
    by_corpus: list[UsageRow]
    by_model: list[UsageRow]
    by_source: list[UsageRow]
    by_payer: list[UsageRow]
    openrouter: OpenRouterBalanceOut | None = Field(
        default=None, description="Only when the engine's LLM key is an OpenRouter key"
    )
    # BIL1 providers
    providers: ProviderSummaryOut | None = Field(
        default=None,
        description="Every provider's one-line state; the full cards are on "
        "GET /api/v1/admin/billing/providers",
    )
    # /BIL1 providers


def _range(start: date | None, end: date | None) -> tuple[date, date]:
    end = end or datetime.now(UTC).date()
    start = start or month_start(end)
    if start > end:
        raise APIError(422, "start must be on or before end", "invalid_range")
    if (end - start).days > _MAX_RANGE_DAYS:
        raise APIError(422, "Range is limited to one year", "range_too_long")
    return start, end


@router.get("", response_model=CostsResponse)
async def get_costs(
    request: Request,
    session: AsyncSession = Depends(get_session),
    start: date | None = Query(None, description="First day (UTC), default the 1st of this month"),
    end: date | None = Query(None, description="Last day (UTC), default today"),
) -> CostsResponse:
    """Platform costs: today, this month and the range, and the range by customer, knowledge
    base, model, source and payer. Every row says whether its price is fully known."""
    start, end = _range(start, end)
    today = datetime.now(UTC).date()
    settings = request.app.state.settings

    async def report(group_by: str, a: date, b: date) -> list[dict[str, Any]]:
        rows = await usage_report(session, corpus_ids=None, start=a, end=b, group_by=group_by)
        return await with_labels(session, group_by, rows)

    by_day_today = await report("day", today, today)
    by_day_month = await report("day", month_start(today), today)
    by_day_range = await report("day", start, end)
    balance = await get_openrouter_balance(settings)
    return CostsResponse(
        start=start,
        end=end,
        usd_inr_rate=settings.usd_inr_rate,
        today=UsageRow(**sum_rows(by_day_today)),
        month=UsageRow(**sum_rows(by_day_month)),
        range=UsageRow(**sum_rows(by_day_range)),
        by_tenant=[UsageRow(**r) for r in await report("tenant", start, end)],
        by_corpus=[UsageRow(**r) for r in await report("corpus", start, end)],
        by_model=[UsageRow(**r) for r in await report("model", start, end)],
        by_source=[UsageRow(**r) for r in await report("source", start, end)],
        by_payer=[UsageRow(**r) for r in await report("payer", start, end)],
        openrouter=(
            OpenRouterBalanceOut.of(balance, settings.openrouter_low_balance_usd)
            if balance is not None
            else None
        ),
        providers=await provider_summary(settings, session),  # BIL1 providers
    )


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "One line per usage row"}},
)
async def export_costs(
    request: Request,
    session: AsyncSession = Depends(get_session),
    start: date | None = Query(None, description="First day (UTC), default the 1st of this month"),
    end: date | None = Query(None, description="Last day (UTC), default today"),
) -> Response:
    """The range as CSV: day, customer, knowledge base, source, payer, model, requests,
    tokens, cost in dollars and rupees, and how many requests had no known price."""
    start, end = _range(start, end)
    body = await costs_csv(session, start, end, request.app.state.settings.usd_inr_rate)
    name = f"costs-{start.isoformat()}-to-{end.isoformat()}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )
