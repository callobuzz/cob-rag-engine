"""Usage metering: record every answered request, report totals for billing.

Recording is best-effort by design. A failed meter write is logged and
swallowed: a visitor should never see an error because the usage table was
briefly unavailable. The reading side is the scoped `GET /api/v1/usage`.

Cost is what the request really cost (`llm.cost`): the provider's own figure, summed over every
model call the request made (answer, rewrites, classification, re-rank), stored as integer USD
micro-units. A request whose price is unknown counts in `unpriced_requests` and adds nothing
to the cost, so a total is either exact or flagged (`cost_known=false`), never a silent zero.
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_question_count import TenantQuestionCount
from callobuzz_rag.db.models.usage import UsageDaily
from callobuzz_rag.llm.cost import (
    MICRO,
    RequestCost,
    bare_model,
    priced_cost,
)
from callobuzz_rag.llm.cost import (
    request_cost_of as request_cost_of,  # re-exported: the call sites meter through here
)

logger = logging.getLogger(__name__)

SOURCE_API = "api"
SOURCE_STREAM = "stream"
SOURCE_CONVERSATION = "conversation"
# A golden-set test run (conversation C7): answers count as questions, judge calls do not.
SOURCE_EVAL = "eval"
# The model calls of a guided setup (conversation C8): tokens and cost, no question counted.
SOURCE_SETUP = "setup"
# One-off helper calls of the assistant editor ("Improve this rule", C9): spend only, no question.
SOURCE_ASSISTANT = "assistant"
# The new-lead summary written by the worker (conversation C4): spend only, never a question.
SOURCE_LEAD_SUMMARY = "lead_summary"
# The summary of a handoff written by the worker (conversation C5): spend only, never a question.
SOURCE_HANDOFF_SUMMARY = "handoff_summary"
# COB, the built-in assistant (COB2): a signed-in person's question / the public COB (COB6).
SOURCE_COB = "cob"
SOURCE_COB_PUBLIC = "cob_public"
# LP2 landing
SOURCE_LANDING = "landing"  # the public website's "generate a conversation" helper call
# /LP2 landing
WIDGET_SOURCE_PREFIX = "widget:"

PAYER_OPERATOR = "operator"
PAYER_BYOK = "byok"
PAYERS = (PAYER_OPERATOR, PAYER_BYOK)

_UPSERT_CONSTRAINT = "uq_usage_daily_day_corpus_tenant_source_payer_model"


def widget_source(key_id: uuid.UUID | str) -> str:
    return f"{WIDGET_SOURCE_PREFIX}{key_id}"


@lru_cache(maxsize=1)
def _encoder() -> Any:
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def estimate_tokens(text: str) -> int:
    """Token count for streamed replies, where providers report no usage."""
    if not text:
        return 0
    try:
        return len(_encoder().encode(text, disallowed_special=()))
    except Exception:
        return max(1, len(text) // 4)


def payer_of(resolved_llm: Any) -> str:
    """`operator` or `byok` from a resolved LLM config (operator when it does not say)."""
    payer = getattr(resolved_llm, "payer", None)
    return payer if payer in PAYERS else PAYER_OPERATOR


async def record_usage(
    session: AsyncSession,
    *,
    corpus_id: uuid.UUID,
    tenant_id: uuid.UUID,
    source: str,
    prompt_tokens: int,
    completion_tokens: int,
    model: str | None = None,
    commit: bool = True,
    requests: int = 1,
    cost: RequestCost | None = None,
    payer: str = PAYER_OPERATOR,
    request_id: uuid.UUID | None = None,
) -> None:
    """Add one answered request to today's row for (corpus, source, payer, model), and count
    it in the tenant's monthly question total (`tenant_question_counts`, the monthly cap's
    source; accounts A6).

    `cost` is the request's `RequestCost` (see `request_cost_of`); its token counts and cost
    replace `prompt_tokens`/`completion_tokens`. Without it the cost comes from a price list
    for `model` and is unknown when the list does not have it.

    `requests=0` adds tokens and cost without counting another request: an
    auxiliary model call made while answering one (a conversation's auto-title). It
    does not touch the monthly count.

    `request_id` is the idempotency key of billing: the same id is metered and charged once, a
    repeat is skipped entirely (no usage row, no monthly count). Call sites that can repeat the
    same work pass a stable id; without one every call is its own request.
    """
    try:
        if cost is None:
            if prompt_tokens == 0 and completion_tokens == 0:
                known_usd: float | None = 0.0
            elif model:
                known_usd = priced_cost(model, None, prompt_tokens, completion_tokens)
            else:
                known_usd = None
            cost = RequestCost(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_micro=round((known_usd or 0.0) * MICRO),
                cost_known=known_usd is not None,
                calls=1,
            )
        elif cost.calls:
            prompt_tokens, completion_tokens = cost.prompt_tokens, cost.completion_tokens
        unpriced = 0 if cost.cost_known else max(requests, 1)
        payer = payer if payer in PAYERS else PAYER_OPERATOR
        now = datetime.now(UTC)
        day = now.date()
        # BIL3 charge
        counts_toward_caps = True
        try:
            from callobuzz_rag.billing.charging import UsageEvent, charge_usage

            charge = await charge_usage(
                session,
                UsageEvent(
                    request_id=request_id or uuid.uuid4(),
                    tenant_id=tenant_id,
                    corpus_id=corpus_id,
                    source=source,
                    payer=payer,
                    model=bare_model(model or ""),
                    questions=requests,
                    cost_usd_micro=cost.cost_micro,
                    cost_known=cost.cost_known,
                    questions_before=0,
                    at=now,
                ),
            )
            if charge.status == "duplicate":
                logger.debug("Usage for request %s was already metered", request_id)
                if commit:
                    await session.commit()
                return
            counts_toward_caps = charge.counts_toward_caps
        except Exception as exc:
            logger.warning(
                "billing charge failed for tenant %s (%s)", tenant_id, type(exc).__name__
            )
        # /BIL3 charge
        stmt = insert(UsageDaily).values(
            day=day,
            corpus_id=corpus_id,
            tenant_id=tenant_id,
            source=source[:64],
            payer=payer,
            model=bare_model(model or "")[:128],
            requests=requests,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=cost.reasoning_tokens,
            cost_micro=cost.cost_micro,
            unpriced_requests=unpriced,
        )
        stmt = stmt.on_conflict_do_update(
            constraint=_UPSERT_CONSTRAINT,
            set_={
                "requests": UsageDaily.requests + requests,
                "prompt_tokens": UsageDaily.prompt_tokens + prompt_tokens,
                "completion_tokens": UsageDaily.completion_tokens + completion_tokens,
                "reasoning_tokens": UsageDaily.reasoning_tokens + cost.reasoning_tokens,
                "cost_micro": UsageDaily.cost_micro + cost.cost_micro,
                "unpriced_requests": UsageDaily.unpriced_requests + unpriced,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)
        if requests > 0 and counts_toward_caps:
            month_stmt = insert(TenantQuestionCount).values(
                tenant_id=tenant_id, month=day.replace(day=1), questions=requests
            )
            month_stmt = month_stmt.on_conflict_do_update(
                index_elements=["tenant_id", "month"],
                set_={
                    "questions": TenantQuestionCount.questions + requests,
                    "updated_at": func.now(),
                },
            )
            await session.execute(month_stmt)
        if commit:
            await session.commit()
    except Exception:
        logger.warning("Usage metering failed for corpus %s", corpus_id, exc_info=True)


async def record_usage_new_session(session_factory: Any, **kwargs: Any) -> None:
    """record_usage in its own session — for code running after a response
    has started streaming, when the request's session may already be closed."""
    if session_factory is None:
        return
    try:
        async with session_factory() as session:
            await record_usage(session, **kwargs)
    except Exception:
        logger.warning("Usage metering session failed", exc_info=True)


GROUP_COLUMNS = {
    "day": UsageDaily.day,
    "corpus": UsageDaily.corpus_id,
    "source": UsageDaily.source,
    "tenant": UsageDaily.tenant_id,
    "payer": UsageDaily.payer,
    "model": UsageDaily.model,
}


def _row_dict(row: Any) -> dict[str, Any]:
    unpriced = int(getattr(row, "unpriced_requests", 0) or 0)
    return {
        "key": str(row.key) if row.key is not None else None,
        "requests": int(row.requests or 0),
        "prompt_tokens": int(row.prompt_tokens or 0),
        "completion_tokens": int(row.completion_tokens or 0),
        "reasoning_tokens": int(getattr(row, "reasoning_tokens", 0) or 0),
        "cost_usd": round(int(getattr(row, "cost_micro", 0) or 0) / MICRO, 6),
        "unpriced_requests": unpriced,
        "cost_known": unpriced == 0,
    }


async def usage_report(
    session: AsyncSession,
    *,
    corpus_ids: Sequence[uuid.UUID] | None,
    start: date,
    end: date,
    group_by: str,
    exclude_operator: bool = False,
    payer: str | None = None,
    tenant_id: uuid.UUID | None = None,
    cob_scope: tuple[uuid.UUID, uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    """Totals between start and end (inclusive), grouped by day, corpus, source, tenant,
    payer or model.

    `corpus_ids=None` means unrestricted (admin); an empty sequence means
    nothing (a tenant that owns no corpora). `exclude_operator` leaves out the operator's
    own workspace (the house account is not a customer); the raw rows are untouched, and
    `group_by="tenant"` rows always say whether their tenant is the operator workspace
    (`is_operator`, else null). `payer` keeps one payer's rows; `tenant_id` one tenant's.

    `cob_scope=(system corpus id, workspace id)` adds that workspace's own COB rows to a
    corpus-restricted report: COB answers every workspace from one system knowledge base,
    so its rows belong to no corpus the workspace owns, and only the rows written for
    this workspace (`tenant_id`) are added.
    """
    if corpus_ids is not None and not corpus_ids and cob_scope is None:
        return []

    column = GROUP_COLUMNS[group_by]

    stmt = (
        select(
            column.label("key"),
            func.sum(UsageDaily.requests).label("requests"),
            func.sum(UsageDaily.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageDaily.completion_tokens).label("completion_tokens"),
            func.sum(UsageDaily.reasoning_tokens).label("reasoning_tokens"),
            func.sum(UsageDaily.cost_micro).label("cost_micro"),
            func.sum(UsageDaily.unpriced_requests).label("unpriced_requests"),
        )
        .where(UsageDaily.day >= start, UsageDaily.day <= end)
        .group_by(column)
        .order_by(column)
    )
    if corpus_ids is not None:
        in_scope: ColumnElement[bool] = UsageDaily.corpus_id.in_(list(corpus_ids))
        if cob_scope is not None:
            in_scope = or_(
                in_scope,
                and_(
                    UsageDaily.corpus_id == cob_scope[0],
                    UsageDaily.tenant_id == cob_scope[1],
                ),
            )
        stmt = stmt.where(in_scope)
    if payer is not None:
        stmt = stmt.where(UsageDaily.payer == payer)
    if tenant_id is not None:
        stmt = stmt.where(UsageDaily.tenant_id == tenant_id)
    if exclude_operator:
        stmt = stmt.where(
            UsageDaily.tenant_id.not_in(select(Tenant.id).where(Tenant.is_operator.is_(True)))
        )

    rows = (await session.execute(stmt)).all()
    operator_ids: set[Any] = set()
    if group_by == "tenant":
        operator_ids = set(
            (await session.execute(select(Tenant.id).where(Tenant.is_operator.is_(True))))
            .scalars()
            .all()
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        item = _row_dict(row)
        if group_by == "tenant":
            item["is_operator"] = row.key in operator_ids
        out.append(item)
    return out


def sum_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Totals of report rows, in the same shape."""
    unpriced = sum(r["unpriced_requests"] for r in rows)
    return {
        "key": None,
        "requests": sum(r["requests"] for r in rows),
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
        "reasoning_tokens": sum(r["reasoning_tokens"] for r in rows),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "unpriced_requests": unpriced,
        "cost_known": unpriced == 0,
    }


def month_start(today: date | None = None) -> date:
    return (today or datetime.now(UTC).date()).replace(day=1)


def own_key_cost_only(
    rows: Sequence[dict[str, Any]], own_rows: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """`rows` with cost, unpriced count and `cost_known` taken from `own_rows` (the same report
    restricted to the customer's own key). What the operator's key paid is the operator's
    business: a row with no own-key spend reads as a known zero, while its requests and tokens
    stay."""
    own = {r["key"]: r for r in own_rows}
    out: list[dict[str, Any]] = []
    for row in rows:
        mine = own.get(row["key"])
        out.append(
            {
                **row,
                "cost_usd": mine["cost_usd"] if mine else 0.0,
                "unpriced_requests": mine["unpriced_requests"] if mine else 0,
                "cost_known": mine["cost_known"] if mine else True,
            }
        )
    return out
