"""What the dashboard header and the Costs page read: this month's consumption per principal,
and the platform-wide cost breakdowns.

Three header views, chosen by who is looking:

- `platform`: a super admin or the operator's own workspace. The month's cost of every request
  the engine's own LLM key paid (`payer=operator`), across all customers, plus the OpenRouter
  key's balance when that is the engine's provider.
- `operator_key`: a customer whose answers run on the operator's key. They see questions used
  against the plan's monthly cap (or just the count when the plan is unlimited), never the
  operator's cost.
- `byok`: a customer whose knowledge bases answer with their own LLM key. They see their own
  AI spend for the month.
"""

import csv
import io
import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.caps import question_quota, questions_this_month
from callobuzz_rag.analytics.usage import (
    PAYER_BYOK,
    PAYER_OPERATOR,
    month_start,
    sum_rows,
    usage_report,
)
from callobuzz_rag.billing.byok import byok_allowed  # P5 plans
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.usage import UsageDaily

VIEW_PLATFORM = "platform"
VIEW_OPERATOR_KEY = "operator_key"
VIEW_BYOK = "byok"


async def _all_corpora_use_own_key(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """A tenant that has not asked anything yet but whose every knowledge base stores its own
    LLM key is a BYOK customer."""
    total, keyed = (
        await session.execute(
            select(
                func.count(Corpus.id),
                func.count(Corpus.id).filter(
                    func.coalesce(Corpus.llm_config["api_key_encrypted"].as_string(), "") != ""
                ),
            ).where(Corpus.tenant_id == tenant_id)
        )
    ).one()
    return bool(total) and total == keyed


async def platform_consumption(session: AsyncSession, today: date) -> dict[str, Any]:
    """The month's operator-paid cost and question count across the platform."""
    rows = await usage_report(
        session,
        corpus_ids=None,
        start=month_start(today),
        end=today,
        group_by="payer",
        payer=PAYER_OPERATOR,
    )
    total = sum_rows(rows)
    return {
        "view": VIEW_PLATFORM,
        "spend_usd": total["cost_usd"],
        "spend_known": total["cost_known"],
        "unpriced_requests": total["unpriced_requests"],
        "questions": total["requests"],
        "question_limit": None,
    }


async def tenant_consumption(
    session: AsyncSession, tenant_id: uuid.UUID, today: date
) -> dict[str, Any]:
    """One customer's month: their questions against the cap, or their own spend."""
    month = month_start(today)
    rows = await usage_report(
        session,
        corpus_ids=None,
        start=month,
        end=today,
        group_by="payer",
        tenant_id=tenant_id,
    )
    by_payer = {r["key"]: r for r in rows}
    byok = by_payer.get(PAYER_BYOK)
    operator = by_payer.get(PAYER_OPERATOR)
    is_byok = (
        byok is not None
        and byok["requests"] > 0
        and (operator is None or operator["requests"] == 0)
    ) or (not rows and await _all_corpora_use_own_key(session, tenant_id))
    # P5 plans: a workspace whose plan has no own-key allowance is answered on the operator's key
    # (its stored key is kept, not used), so its month reads as the operator-key view
    if is_byok and not await byok_allowed(session, tenant_id):
        is_byok = False
    # /P5 plans
    if is_byok:
        spend = sum_rows([byok] if byok else [])
        return {
            "view": VIEW_BYOK,
            "spend_usd": spend["cost_usd"],
            "spend_known": spend["cost_known"],
            "unpriced_requests": spend["unpriced_requests"],
            "questions": spend["requests"],
            "question_limit": None,
        }
    quota = await question_quota(session, tenant_id, month)
    if quota is None:
        questions, limit = await questions_this_month(session, tenant_id), None
    else:
        limit, questions = quota
    return {
        "view": VIEW_OPERATOR_KEY,
        "spend_usd": None,
        "spend_known": True,
        "unpriced_requests": 0,
        "questions": questions,
        "question_limit": limit,
    }


async def with_labels(
    session: AsyncSession, group_by: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add `label` (the customer's or knowledge base's name) to tenant and corpus rows."""
    for row in rows:
        row["label"] = None
    if group_by not in ("tenant", "corpus") or not rows:
        return rows
    ids = [uuid.UUID(r["key"]) for r in rows if r["key"]]
    if group_by == "tenant":
        names = dict(
            (await session.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_(ids)))).all()
        )
    else:
        names = dict(
            (await session.execute(select(Corpus.id, Corpus.name).where(Corpus.id.in_(ids)))).all()
        )
    for row in rows:
        row["label"] = names.get(uuid.UUID(row["key"])) if row["key"] else None
    return rows


def _safe_cell(value: Any) -> Any:
    """A spreadsheet runs a cell that starts with = + - @ as a formula: defuse names."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


async def costs_csv(session: AsyncSession, start: date, end: date, usd_inr_rate: float) -> str:
    """Every usage row of the range, one CSV line each, with names and both currencies."""
    stmt = (
        select(
            UsageDaily.day,
            Tenant.name.label("customer"),
            Corpus.name.label("knowledge_base"),
            UsageDaily.source,
            UsageDaily.payer,
            UsageDaily.model,
            UsageDaily.requests,
            UsageDaily.prompt_tokens,
            UsageDaily.completion_tokens,
            UsageDaily.reasoning_tokens,
            UsageDaily.cost_micro,
            UsageDaily.unpriced_requests,
        )
        .join(Corpus, Corpus.id == UsageDaily.corpus_id)
        .outerjoin(Tenant, Tenant.id == UsageDaily.tenant_id)
        .where(UsageDaily.day >= start, UsageDaily.day <= end)
        .order_by(UsageDaily.day, Tenant.name, Corpus.name, UsageDaily.source, UsageDaily.model)
    )
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(
        [
            "day",
            "customer",
            "knowledge_base",
            "source",
            "payer",
            "model",
            "requests",
            "prompt_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "cost_usd",
            "cost_inr",
            "requests_with_unknown_price",
        ]
    )
    for r in (await session.execute(stmt)).all():
        usd = r.cost_micro / 1_000_000
        writer.writerow(
            [
                r.day.isoformat(),
                _safe_cell(r.customer or ""),
                _safe_cell(r.knowledge_base or ""),
                _safe_cell(r.source),
                r.payer,
                _safe_cell(r.model or ""),
                r.requests,
                r.prompt_tokens,
                r.completion_tokens,
                r.reasoning_tokens,
                f"{usd:.6f}",
                f"{usd * usd_inr_rate:.4f}",
                r.unpriced_requests,
            ]
        )
    return out.getvalue()
