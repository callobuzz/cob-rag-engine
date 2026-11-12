"""GET /api/v1/usage — metered usage for billing and dashboards.

Scoped like everything else: the admin key sees all corpora (optionally one
tenant's), a tenant key sees its own corpora, a corpus key sees itself.

`GET /api/v1/usage/consumption` is the dashboard header's one-line summary of this month.
"""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.analytics.consumption import platform_consumption, tenant_consumption
from callobuzz_rag.analytics.openrouter_balance import OpenRouterBalance, get_openrouter_balance
from callobuzz_rag.analytics.usage import month_start, own_key_cost_only, sum_rows, usage_report
from callobuzz_rag.api.deps import authorized_corpus_ids, get_session, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.billing import BillingMeterOut  # BIL6 meter
from callobuzz_rag.api.schemas.provider_balances import ProviderSummaryOut  # BIL1
from callobuzz_rag.cob.system import get_system_corpus
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])

_MAX_RANGE_DAYS = 366


class UsageRow(BaseModel):
    key: str | None
    label: str | None = Field(
        default=None, description="Costs report only: the customer's or knowledge base's name"
    )
    is_operator: bool | None = None  # group_by=tenant only: the operator's own workspace
    requests: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int = Field(
        default=0, description="Thinking tokens, already counted inside completion_tokens"
    )
    cost_usd: float = Field(
        description="What the requests really cost, in US dollars: the provider's own figure "
        "where it reports one, else a price list. When `cost_known` is false this is only the "
        "priced part."
    )
    unpriced_requests: int = Field(
        default=0, description="Requests whose price could not be found (not counted in cost_usd)"
    )
    cost_known: bool = Field(
        default=True, description="False when some requests have no price: show 'price unknown'"
    )


class UsageResponse(BaseModel):
    start: date
    end: date
    group_by: str
    usd_inr_rate: float = Field(description="Rupees per dollar (RAG_USD_INR_RATE), a fixed rate")
    rows: list[UsageRow]
    totals: UsageRow


class OpenRouterBalanceOut(BaseModel):
    available: bool = Field(description="False when OpenRouter could not be asked just now")
    remaining_usd: float | None = Field(description="Null when OpenRouter did not say")
    limit_usd: float | None
    usage_usd: float | None
    usage_monthly_usd: float | None
    low: bool = Field(description="Remaining balance is under low_threshold_usd")
    low_threshold_usd: float
    # BIL1 openrouter
    limit_reset: Literal["daily", "weekly", "monthly"] | None = Field(
        None, description="How often the key's limit resets"
    )
    resets_at: datetime | None = Field(None, description="When the limit next resets (UTC)")
    usage_daily_usd: float | None = None
    usage_weekly_usd: float | None = None
    account_credits_usd: float | None = Field(
        None, description="Account credits left; needs a management key"
    )
    label: str | None = None
    # /BIL1 openrouter

    @classmethod
    def of(cls, balance: OpenRouterBalance, threshold: float) -> "OpenRouterBalanceOut":
        return cls(
            available=balance.available,
            remaining_usd=balance.remaining_usd,
            limit_usd=balance.limit_usd,
            usage_usd=balance.usage_usd,
            usage_monthly_usd=balance.usage_monthly_usd,
            low=balance.low,
            low_threshold_usd=threshold,
            # BIL1 openrouter
            limit_reset=balance.limit_reset,
            resets_at=balance.resets_at,
            usage_daily_usd=balance.usage_daily_usd,
            usage_weekly_usd=balance.usage_weekly_usd,
            account_credits_usd=balance.account_credits_usd,
            label=balance.label,
            # /BIL1 openrouter
        )


class ConsumptionResponse(BaseModel):
    view: Literal["platform", "operator_key", "byok"] = Field(
        description="platform: operator-paid cost across the platform (super admin or the "
        "operator's workspace). operator_key: a customer on the operator's key (questions vs "
        "the plan cap). byok: a customer on their own LLM key (their spend)."
    )
    month: date = Field(description="First day of the UTC month these figures cover")
    usd_inr_rate: float
    spend_usd: float | None = Field(description="platform and byok views; null for operator_key")
    spend_known: bool
    unpriced_requests: int
    questions: int
    question_limit: int | None = Field(description="The plan's monthly cap; null when unlimited")
    openrouter: OpenRouterBalanceOut | None = Field(
        default=None,
        description="platform view only, and only when the engine's LLM key is an OpenRouter key",
    )
    # BIL1 providers
    providers: ProviderSummaryOut | None = Field(
        default=None,
        description="platform view only: every configured provider and the one to look at first",
    )
    # /BIL1 providers
    # BIL6 meter
    billing: BillingMeterOut | None = Field(
        default=None,
        description="tenant view only: the balance (prepaid) or credit (postpaid) of a billed "
        "workspace; amounts are null for a member unless the plan lets members see money",
    )
    # /BIL6 meter


# BIL6 meter
async def _billing_meter(
    request: Request, session: AsyncSession, tenant_id: uuid.UUID
) -> BillingMeterOut | None:
    """The header's balance or credit for a billed workspace. Never fails the header: a billing
    problem shows no meter, not an error."""
    from callobuzz_rag.billing.summary import billing_meter

    try:
        meter = await billing_meter(
            session, tenant_id, is_member=getattr(request.state, "role", None) == "member"
        )
    except Exception:
        logger.warning("Billing meter failed", exc_info=True)
        return None
    return BillingMeterOut(**meter) if meter else None


# /BIL6 meter


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


async def _is_operator_workspace(session: AsyncSession, tenant_id: uuid.UUID | None) -> bool:
    if tenant_id is None:
        return False
    tenant = await session.get(Tenant, tenant_id)
    return bool(tenant is not None and getattr(tenant, "is_operator", False) is True)


@router.get("", response_model=UsageResponse)
async def get_usage(
    request: Request,
    session: AsyncSession = Depends(get_session),
    corpus_id: uuid.UUID | None = Query(None, description="Limit to one corpus"),
    tenant_id: uuid.UUID | None = Query(None, description="Admin only: limit to one tenant"),
    start: date | None = Query(None, description="First day (UTC), default 30 days ago"),
    end: date | None = Query(None, description="Last day (UTC), default today"),
    group_by: Literal["day", "corpus", "source", "tenant", "payer", "model"] = Query("day"),
    payer: Literal["operator", "byok"] | None = Query(
        None,
        description="Only requests paid by the engine's own key (`operator`) or by a key the "
        "knowledge base stores or the request sent (`byok`)",
    ),
    exclude_operator: bool = Query(
        False,
        description="Leave out the operator's own workspace (not a customer); the usage "
        "itself is still recorded and `group_by=tenant` rows carry `is_operator`",
    ),
) -> UsageResponse:
    """Requests, tokens and the real LLM cost.

    `source` values: `api` (POST /query), `stream` (POST /query/stream),
    `conversation` (conversation messages, JSON and streamed), `cob` (your questions to
    COB, the built-in assistant) and `widget:<widget key id>` (the website chat widget).

    `cost_usd` is what the requests really cost: the provider's own figure (OpenRouter's
    `usage.cost`), summed over every model call a request made, rewrites and re-ranking
    included. A request whose price is unknown is counted in `unpriced_requests` and adds
    no cost; `cost_known` is then false and a screen should say "price unknown", never zero.
    """
    end = end or datetime.now(UTC).date()
    start = start or end - timedelta(days=29)
    if start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")
    if (end - start).days > _MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail="Range is limited to one year")

    scope = resolve_scope(request)
    ids = await authorized_corpus_ids(scope, corpus_id, session)

    if tenant_id is not None:
        if not scope.is_admin and tenant_id != scope.tenant_id:
            raise HTTPException(status_code=403, detail="Not allowed to read this tenant")
        rows = await session.execute(select(Corpus.id).where(Corpus.tenant_id == tenant_id))
        tenant_ids = set(rows.scalars().all())
        ids = [i for i in (ids if ids is not None else tenant_ids) if i in tenant_ids]

    # COB (the built-in assistant) answers from a knowledge base no workspace owns; a
    # workspace still reads the COB questions it asked, and only those.
    # An admin reading one workspace (`tenant_id=`) sees the same COB questions that
    # workspace's owner sees; a plain admin view needs no scope (it reads every row).
    cob_scope = None
    if corpus_id is None and ids is not None and scope.corpus_id is None:
        target = tenant_id if scope.is_admin else scope.tenant_id
        if target is not None and (scope.is_admin or tenant_id in (None, scope.tenant_id)):
            system = await get_system_corpus(session)
            if system is not None:
                cob_scope = (system.id, target)

    report = {
        "corpus_ids": ids,
        "cob_scope": cob_scope,
        "start": start,
        "end": end,
        "group_by": group_by,
        "exclude_operator": exclude_operator,
    }
    rows_out = await usage_report(session, payer=payer, **report)  # type: ignore[arg-type]
    if not scope.is_admin and not await _is_operator_workspace(session, scope.tenant_id):
        # A customer reads its own spend, not what the operator's key paid for its answers.
        own_rows = await usage_report(session, payer="byok", **report)  # type: ignore[arg-type]
        rows_out = own_key_cost_only(rows_out, own_rows)
    return UsageResponse(
        start=start,
        end=end,
        group_by=group_by,
        usd_inr_rate=_settings(request).usd_inr_rate,
        rows=[UsageRow(**r) for r in rows_out],
        totals=UsageRow(**sum_rows(rows_out)),
    )


@router.get("/consumption", response_model=ConsumptionResponse)
async def get_consumption(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ConsumptionResponse:
    """This month at a glance, shaped for who is asking (the dashboard header).

    A super admin or the operator's own workspace gets the month's operator-paid cost across
    the platform and, when the engine's LLM key is an OpenRouter key, its remaining balance
    (cached five minutes server-side). A customer on the operator's key gets the questions
    used against the plan's monthly cap. A customer on their own LLM key gets their own spend.
    Figures are in US dollars; `usd_inr_rate` converts them for display.
    """
    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        # A knowledge-base key reads that knowledge base through GET /usage; the header's
        # month summary spans a whole workspace (or the platform), so it is never for one.
        raise APIError(
            403, "The header summary needs a workspace or admin credential", "scope_forbidden"
        )
    settings = _settings(request)
    today = datetime.now(UTC).date()

    platform = scope.is_admin
    if not platform:
        if scope.tenant_id is None:
            raise APIError(403, "No workspace to report on", "no_workspace")
        platform = await _is_operator_workspace(session, scope.tenant_id)

    balance_out: OpenRouterBalanceOut | None = None
    providers_out: ProviderSummaryOut | None = None  # BIL1 providers
    billing_out: BillingMeterOut | None = None  # BIL6 meter
    if platform:
        data = await platform_consumption(session, today)
        balance = await get_openrouter_balance(settings)
        if balance is not None:
            balance_out = OpenRouterBalanceOut.of(balance, settings.openrouter_low_balance_usd)
        from callobuzz_rag.api.routes.provider_balances import provider_summary  # BIL1 providers

        providers_out = await provider_summary(settings, session)  # BIL1 providers
    else:
        assert scope.tenant_id is not None
        data = await tenant_consumption(session, scope.tenant_id, today)
        billing_out = await _billing_meter(request, session, scope.tenant_id)  # BIL6 meter
    return ConsumptionResponse(
        month=month_start(today),
        usd_inr_rate=settings.usd_inr_rate,
        openrouter=balance_out,
        providers=providers_out,  # BIL1 providers
        billing=billing_out,  # BIL6 meter
        **data,
    )
