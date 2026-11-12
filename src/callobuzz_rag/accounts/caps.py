"""The four plan limits, enforced (accounts A6).

A tenant's **effective** limits are its plan's, except the knowledge-base limit, which a
per-tenant `tenants.max_corpora` overrides when set. NULL is unlimited everywhere.

- Knowledge bases: `check_kb_room` on create and on moving one into a tenant.
- Questions per UTC calendar month: `enforce_question_cap` on every route that answers
  (`/query`, `/query/stream`, conversation messages, widget chat), after the corpus and
  its ownership are resolved and **before** the query cache, so a capped tenant gets no
  answer at all. The count is `tenant_question_counts`, which `record_usage` increments.
  The admin key and super-admin sessions are not capped (their questions still count).
  Search-only `/query/retrieve` is never capped.
- Active documents (a document with an `active` version), per tenant across all its
  knowledge bases: `check_document_room` on every path that can add one, with the number
  that call would add (re-ingesting or updating an existing document adds none).
- Widget keys, per tenant: `check_widget_key_room` on create (rotation adds none).

The document limit is **hard**. The check counts committed active documents plus what
the tenant's in-flight ingest jobs will add (`ingest_jobs.pending_new_documents`, set
when a job is queued), in one statement, under a per-tenant transaction-scoped advisory
lock: two concurrent requests serialise, and the caller creates its job (with the number
this check returns) before its transaction releases the lock. The worker re-checks just
before it creates a document (`ensure_document_room_for_job`) and fails the job with
`document_limit_reached` if the limit no longer has room. A tenant with no document
limit takes neither the lock nor the count.

The other caps are soft: the check runs when the request arrives, so requests already in
flight can overshoot by what is in flight. Lowering a limit below current usage blocks
new additions only; it removes nothing.
"""

import logging
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, and_, exists, func, or_, select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.limits import (  # BIL5 limits
    active_grant_sum,
    apply_limit_adjustments,
)
from callobuzz_rag.core.exceptions import DocumentLimitReachedError
from callobuzz_rag.db.models.billing_account import BillingAccount  # BIL5 limits
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.db.models.plan import LIMIT_FIELDS, Plan  # BIL5 limits
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_question_count import TenantQuestionCount
from callobuzz_rag.db.models.widget_key import WidgetKey

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

WIDGET_UNAVAILABLE = "This assistant is not available right now."

AddsFn = Callable[[], Awaitable[int]]

# Ingest job statuses that still lead to documents being written. `pending_review` is
# not one: the job adds nothing unless confirmed, and the confirmation is checked.
IN_FLIGHT_STATUSES = (
    "queued",
    "uploading",
    "parsing",
    "chunking",
    "enriching",
    "embedding",
    "storing",
)
# A job still "in flight" a day after it was created was lost (a worker died mid-job):
# it stops holding the tenant's allowance. The worker re-checks if it ever runs.
IN_FLIGHT_WINDOW = timedelta(hours=24)
# First key of the two-int advisory lock; the second is hashtext(tenant id). A hash
# collision only makes two tenants' document checks wait for each other.
_DOCUMENT_LOCK_NAMESPACE = 0x0A6D0C


@dataclass(frozen=True)
class Limits:
    """Effective limits; None = unlimited."""

    max_knowledge_bases: int | None = None
    monthly_questions: int | None = None
    max_documents: int | None = None
    max_widget_keys: int | None = None
    max_upload_mb: int | None = None
    max_document_tokens: int | None = None
    count_cached_answers: bool = True


@dataclass(frozen=True)
class Usage:
    month: date
    questions: int
    knowledge_bases: int
    documents: int
    widget_keys: int


# ------------------------------------------------------------------- clock


def _utc(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


def current_month(now: datetime | None = None) -> date:
    """The first day of the current UTC calendar month."""
    return _utc(now).date().replace(day=1)


def next_month_start(now: datetime | None = None) -> datetime:
    """00:00 UTC on the first day of the next month."""
    first = current_month(now)
    year, month = (first.year + 1, 1) if first.month == 12 else (first.year, first.month + 1)
    return datetime(year, month, 1, tzinfo=UTC)


def seconds_until_next_month(now: datetime | None = None) -> int:
    """Whole seconds (at least 1) until the monthly count starts again."""
    moment = _utc(now)
    return max(1, math.ceil((next_month_start(moment) - moment).total_seconds()))


def _iso_z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------- reading


# BIL5 limits
def _grant_columns(start: datetime, end: datetime | None = None) -> list[Any]:
    """One scalar subquery per limit field, in `LIMIT_FIELDS` order: what the tenant's active
    add-on grants add to it (see `billing/limits.py`). Correlated to `tenants`."""
    return [active_grant_sum(Tenant.id, field, start, end) for field in LIMIT_FIELDS]


async def effective_limits(
    session: "AsyncSession", tenant_id: uuid.UUID, now: datetime | None = None
) -> Limits:
    """The tenant's limits: the plan's (`tenants.max_corpora` replaces the knowledge-base
    one), then the billing account's own overrides, plus the add-ons in force now.

    One statement. Applies even while billing is switched off (they are limits, not money).
    Never read `Plan.*` limits directly."""
    row = (
        await session.execute(
            select(
                Tenant.max_corpora,
                Plan.max_knowledge_bases,
                Plan.monthly_questions,
                Plan.max_documents,
                Plan.max_widget_keys,
                Plan.max_upload_mb,
                Plan.max_document_tokens,
                Plan.count_cached_answers,
                BillingAccount.limit_overrides,
                *_grant_columns(_utc(now)),
            )
            .select_from(Tenant)
            .join(Plan, Plan.id == Tenant.plan_id)
            .outerjoin(BillingAccount, BillingAccount.tenant_id == Tenant.id)
            .where(Tenant.id == tenant_id)
        )
    ).one_or_none()
    if row is None:
        return Limits()
    override, max_kbs, monthly, max_docs, max_keys, max_mb, max_tokens, count_cached = row[:8]
    base = Limits(
        max_knowledge_bases=override if override is not None else max_kbs,
        monthly_questions=monthly,
        max_documents=max_docs,
        max_widget_keys=max_keys,
        max_upload_mb=max_mb,
        max_document_tokens=max_tokens,
        count_cached_answers=bool(count_cached),
    )
    return apply_limit_adjustments(
        base, dict(row[8] or {}), dict(zip(LIMIT_FIELDS, row[9:], strict=True))
    )


# /BIL5 limits


async def questions_this_month(
    session: "AsyncSession", tenant_id: uuid.UUID, now: datetime | None = None
) -> int:
    used = (
        await session.execute(
            select(TenantQuestionCount.questions).where(
                TenantQuestionCount.tenant_id == tenant_id,
                TenantQuestionCount.month == current_month(now),
            )
        )
    ).scalar_one_or_none()
    return int(used or 0)


async def knowledge_base_count(session: "AsyncSession", tenant_id: uuid.UUID) -> int:
    count = (
        await session.execute(select(func.count(Corpus.id)).where(Corpus.tenant_id == tenant_id))
    ).scalar()
    return int(count or 0)


def _active_documents(tenant_id: uuid.UUID) -> Select[Any]:
    active_version = exists().where(
        DocumentVersion.document_id == Document.id, DocumentVersion.status == "active"
    )
    # System documents (the assistant's business card, conversation C2) are not the tenant's
    # content and never use the plan's document allowance.
    not_system = or_(Document.external_id.is_(None), Document.external_id.not_like("system:%"))
    return (
        select(func.count(Document.id.distinct()))
        .join(Corpus, Corpus.id == Document.corpus_id)
        .where(Corpus.tenant_id == tenant_id, active_version, not_system)
    )


def _queued_documents(tenant_id: uuid.UUID, exclude_job_id: uuid.UUID | None = None) -> Select[Any]:
    """What the tenant's in-flight ingest jobs will add (their `pending_new_documents`)."""
    stmt = (
        select(func.coalesce(func.sum(IngestJob.pending_new_documents), 0))
        .join(Corpus, Corpus.id == IngestJob.corpus_id)
        .where(
            Corpus.tenant_id == tenant_id,
            IngestJob.status.in_(IN_FLIGHT_STATUSES),
            IngestJob.pending_new_documents > 0,
            IngestJob.created_at > func.now() - IN_FLIGHT_WINDOW,
        )
    )
    if exclude_job_id is not None:
        stmt = stmt.where(IngestJob.id != exclude_job_id)
    return stmt


async def active_document_count(session: "AsyncSession", tenant_id: uuid.UUID) -> int:
    """Documents of the tenant's knowledge bases that have an `active` version."""
    count = (await session.execute(_active_documents(tenant_id))).scalar()
    return int(count or 0)


async def document_usage(
    session: "AsyncSession", tenant_id: uuid.UUID, *, exclude_job_id: uuid.UUID | None = None
) -> tuple[int, int]:
    """(active documents, documents in-flight jobs will add), read in ONE statement.

    One statement is one snapshot: a job that completes meanwhile (its documents become
    active and it leaves the in-flight set in the same commit) is counted exactly once,
    never in both halves nor in neither.
    """
    row = (
        await session.execute(
            select(
                _active_documents(tenant_id).scalar_subquery(),
                _queued_documents(tenant_id, exclude_job_id).scalar_subquery(),
            )
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


async def lock_tenant_documents(session: "AsyncSession", tenant_id: uuid.UUID) -> None:
    """Serialise document-limit checks of one tenant until this transaction ends.

    A transaction-scoped advisory lock: released by the caller's commit or rollback, so
    the job (or document) the checked call creates is committed before the next check
    of the same tenant reads the count.
    """
    await session.execute(
        select(func.pg_advisory_xact_lock(_DOCUMENT_LOCK_NAMESPACE, func.hashtext(str(tenant_id))))
    )


async def widget_key_count(session: "AsyncSession", tenant_id: uuid.UUID) -> int:
    """Every widget key of the tenant's knowledge bases, enabled or not (a disabled key
    can be enabled again without passing the create check)."""
    count = (
        await session.execute(
            select(func.count(WidgetKey.id))
            .join(Corpus, Corpus.id == WidgetKey.corpus_id)
            .where(Corpus.tenant_id == tenant_id)
        )
    ).scalar()
    return int(count or 0)


async def usage_snapshot(
    session: "AsyncSession", tenant_id: uuid.UUID, now: datetime | None = None
) -> Usage:
    return Usage(
        month=current_month(now),
        questions=await questions_this_month(session, tenant_id, now),
        knowledge_bases=await knowledge_base_count(session, tenant_id),
        documents=await active_document_count(session, tenant_id),
        widget_keys=await widget_key_count(session, tenant_id),
    )


# --------------------------------------------------------------- enforcing


async def _support_contact(request: "Request") -> str:
    """The operator's support e-mail from branding, else "your provider". Best-effort."""
    try:
        from callobuzz_rag.accounts.settings_store import get_engine_settings

        factory = getattr(request.app.state, "session_factory", None)
        if factory is not None:
            email = (await get_engine_settings(factory)).branding.support_email
            if email:
                return str(email)
    except Exception:
        logger.warning("Could not read branding for the question-cap message", exc_info=True)
    return "your provider"


async def question_quota(
    session: "AsyncSession", tenant_id: uuid.UUID, month: date, *, lock: bool = False
) -> tuple[int, int] | None:
    """(monthly limit, questions used in `month`) in one query; None when unlimited.

    `lock=True` first takes the month's count row for update (creating it at 0 when missing),
    so a writer that prices by the count and then increments it (the usage meter) serialises
    with the other answers of the tenant: two questions at the edge of the allowance cannot
    both read the same count. The lock lasts until the caller's transaction ends."""
    if lock:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        await session.execute(
            pg_insert(TenantQuestionCount)
            .values(tenant_id=tenant_id, month=month, questions=0)
            .on_conflict_do_nothing(index_elements=["tenant_id", "month"])
        )
        await session.execute(
            select(TenantQuestionCount.questions)
            .where(TenantQuestionCount.tenant_id == tenant_id, TenantQuestionCount.month == month)
            .with_for_update()
        )
    # BIL5 limits: the account's override replaces the plan's; question packs granted in
    # `month` add to it (a pack lasts until the next UTC month start).
    month_start = datetime(month.year, month.month, 1, tzinfo=UTC)
    row = (
        await session.execute(
            select(
                Plan.monthly_questions,
                TenantQuestionCount.questions,
                BillingAccount.limit_overrides,
                active_grant_sum(
                    Tenant.id, "monthly_questions", month_start, next_month_start(month_start)
                ),
            )
            .select_from(Tenant)
            .join(Plan, Plan.id == Tenant.plan_id)
            .outerjoin(BillingAccount, BillingAccount.tenant_id == Tenant.id)
            .outerjoin(
                TenantQuestionCount,
                and_(
                    TenantQuestionCount.tenant_id == Tenant.id,
                    TenantQuestionCount.month == month,
                ),
            )
            .where(Tenant.id == tenant_id)
        )
    ).one_or_none()
    if row is None:
        return None
    plan_limit, used, overrides, granted = row
    limit = apply_limit_adjustments(
        Limits(monthly_questions=plan_limit), dict(overrides or {}), {"monthly_questions": granted}
    ).monthly_questions
    if limit is None:
        return None
    return int(limit), int(used or 0)
    # /BIL5 limits


async def counts_cached_answers(session: "AsyncSession", tenant_id: uuid.UUID) -> bool:
    """Whether answers served from the query cache count toward the tenant's monthly
    questions (its plan's `count_cached_answers`; true when there is no plan to ask)."""
    return (await effective_limits(session, tenant_id)).count_cached_answers


# BIL4 overage
async def _cap_block(
    session: "AsyncSession",
    tenant_id: uuid.UUID,
    moment: datetime,
    payer: str | None,
) -> tuple[int, int] | None:
    """`(limit, used)` when the monthly cap refuses this answer, else None.

    One indexed query below the cap. At the cap the plan's `overage` decides (billing BIL4):
    `charge` and `allow` let the question through (it is overage), and a BYOK answer on a plan
    that does not count BYOK questions toward the cap is never capped. A workspace without
    pricing, or an exempt one, behaves exactly as before: `block`."""
    quota = await question_quota(session, tenant_id, current_month(moment))
    if quota is None:
        return None
    limit, used = quota
    if used < limit:
        return None
    if await overage_lets_through(session, tenant_id, payer):
        return None
    return limit, used


async def overage_lets_through(
    session: "AsyncSession", tenant_id: uuid.UUID, payer: str | None = None
) -> bool:
    """Whether the plan lets a question past the monthly allowance: its overage is "charge" or
    "allow", or `payer` is a BYOK key the plan does not count toward the cap. False for a
    workspace without pricing, an exempt one, or billing switched off (today's behaviour)."""
    from callobuzz_rag.billing.config import billing_enabled

    if not billing_enabled():
        return False
    from callobuzz_rag.billing.pricing import TenantNotFoundError, effective_pricing

    try:
        eff = await effective_pricing(session, tenant_id)
    except TenantNotFoundError:
        return False
    if eff.exempt:
        return False
    fields = eff.fields
    if payer == "byok" and not fields.byok_counts_toward_caps:
        return True
    return fields.overage != "block"


async def cap_blocks_answers(
    session: "AsyncSession",
    tenant_id: uuid.UUID,
    now: datetime | None = None,
    payer: str | None = None,
) -> bool:
    """Whether the monthly cap refuses an answer now (the same decision as
    `enforce_question_cap`, for callers that degrade instead of raising: COB)."""
    return await _cap_block(session, tenant_id, _utc(now), payer) is not None


# /BIL4 overage


async def enforce_question_cap(
    session: "AsyncSession",
    request: "Request",
    tenant_id: uuid.UUID,
    *,
    widget: bool = False,
    now: datetime | None = None,
    payer: str | None = None,
) -> None:
    """Refuse an answer once the tenant has used its monthly questions.

    One indexed query (plan limit + this month's count). The admin principal (admin key,
    super-admin session) is never refused. `payer` (`operator` | `byok`, None = operator) and
    the plan's overage rule decide whether a tenant at the cap is refused (BIL4).

    Raises:
        APIError 429 `question_cap_reached` with `Retry-After` (seconds to the next UTC
            month). The API body carries `limit`, `used`, `resets_at`; the widget body
            only a polite sentence (visitors must not see the plan or the operator).
    """
    if getattr(request.state, "is_admin", False):
        return
    moment = _utc(now)
    blocked = await _cap_block(session, tenant_id, moment, payer)  # BIL4 overage
    if blocked is None:
        return
    limit, used = blocked
    headers = {"Retry-After": str(seconds_until_next_month(moment))}
    if widget:
        raise APIError(429, WIDGET_UNAVAILABLE, "question_cap_reached", headers=headers)
    contact = await _support_contact(request)
    extra: dict[str, Any] = {
        "limit": limit,
        "used": used,
        "resets_at": _iso_z(next_month_start(moment)),
    }
    raise APIError(
        429,
        f"Monthly question limit reached — contact {contact}",
        "question_cap_reached",
        headers=headers,
        **extra,
    )


async def check_kb_room(session: "AsyncSession", tenant: Tenant) -> None:
    """Room for one more knowledge base in `tenant` (override-aware).

    Raises:
        APIError 409 `kb_limit_reached` (extra `limit`).
    """
    limit = (await effective_limits(session, tenant.id)).max_knowledge_bases
    if limit is None:
        return
    if await knowledge_base_count(session, tenant.id) >= limit:
        raise APIError(
            409, f"Tenant corpus limit reached ({limit})", "kb_limit_reached", limit=limit
        )


async def check_document_room(
    session: "AsyncSession", tenant_id: uuid.UUID, adds: int | AddsFn
) -> int | None:
    """Room for `adds` more active documents in the tenant, counting queued ingests.

    `adds` is a number, or an async function computing it; the function runs only when
    the tenant has a document limit, so an unlimited tenant pays one query (no lock, no
    count). Nothing to add (0) always passes: updating existing content is allowed at
    the cap.

    With a limit and something to add, this takes the tenant's advisory lock (held until
    the caller's transaction ends) and compares active documents + what in-flight jobs
    will add + `adds` with the limit. The caller must create its job, with
    `pending_new_documents` = the returned number, in the same transaction.

    Returns:
        The number of documents the call adds; None when the tenant is unlimited and
        `adds` was a function (not computed).

    Raises:
        APIError 409 `document_limit_reached` (extras `limit`, `used` = active
            documents, `queued` = what in-flight jobs will add, `adding`).
    """
    if isinstance(adds, int) and adds <= 0:
        return 0
    limit = (await effective_limits(session, tenant_id)).max_documents
    if limit is None:
        return adds if isinstance(adds, int) else None
    adding = adds if isinstance(adds, int) else await adds()
    if adding <= 0:
        return 0
    await lock_tenant_documents(session, tenant_id)
    used, queued = await document_usage(session, tenant_id)
    if used + queued + adding > limit:
        raise APIError(
            409,
            f"Document limit reached ({limit})",
            "document_limit_reached",
            limit=limit,
            used=used,
            queued=queued,
            adding=adding,
        )
    return adding


async def ensure_document_room_for_job(
    session: "AsyncSession", corpus_id: uuid.UUID, job_id: uuid.UUID, adding: int
) -> None:
    """The worker's backstop, just before an ingest job creates `adding` new documents.

    Active documents + what the tenant's OTHER in-flight jobs will add + `adding` must fit
    the limit. Jobs admitted by `check_document_room` always pass (what a job creates
    never exceeds what it reserved); this catches a lowered limit, a job older than the
    in-flight window, and jobs queued before the reservation existed. No lock: the count
    is one statement, and every other in-flight job's reservation is already committed.

    Raises:
        DocumentLimitReachedError: the caller fails the job with its `details()`.
    """
    if adding <= 0:
        return
    # BIL5 limits: the effective limit (plan, overrides, add-ons), not the plan's alone.
    tenant_id = (
        await session.execute(select(Corpus.tenant_id).where(Corpus.id == corpus_id))
    ).scalar_one_or_none()
    if tenant_id is None:
        return
    limit = (await effective_limits(session, tenant_id)).max_documents
    # /BIL5 limits
    if limit is None:
        return
    used, queued = await document_usage(session, tenant_id, exclude_job_id=job_id)
    if used + queued + adding > limit:
        raise DocumentLimitReachedError(limit=limit, used=used, queued=queued, adding=adding)


async def check_widget_key_room(session: "AsyncSession", tenant_id: uuid.UUID) -> None:
    """Room for one more widget key in the tenant.

    Raises:
        APIError 409 `widget_key_limit_reached` (extras `limit`, `used`).
    """
    limit = (await effective_limits(session, tenant_id)).max_widget_keys
    if limit is None:
        return
    used = await widget_key_count(session, tenant_id)
    if used >= limit:
        raise APIError(
            409,
            f"Widget key limit reached ({limit})",
            "widget_key_limit_reached",
            limit=limit,
            used=used,
        )


# ------------------------------------------------------------ upload sizes


async def upload_byte_limit(
    session: "AsyncSession", tenant_id: uuid.UUID | None, engine_limit: int
) -> int:
    """Largest upload in bytes: the engine's `RAG_MAX_UPLOAD_BYTES`, or the plan's lower
    `max_upload_mb`. A plan cannot raise the engine's limit."""
    if tenant_id is None:
        return engine_limit
    plan_mb = (await effective_limits(session, tenant_id)).max_upload_mb
    return engine_limit if plan_mb is None else min(engine_limit, plan_mb * 1024 * 1024)


# PIP3 storage
async def check_upload_room(
    session: "AsyncSession",
    tenant_id: uuid.UUID | None,
    adding_bytes: int,
    *,
    enabled: bool = True,
    now: datetime | None = None,
) -> None:
    """Fair-use limits of an upload, next to `upload_byte_limit`: files per day and the
    business's total storage (stored documents plus uploads still in flight). Nothing is checked
    for an unlimited business (one query for the effective limits, no counting, no lock), the
    operator's workspace, or with fair use switched off. A limited business is checked under
    the tenant lock (the same one as `check_document_room`) that the caller's commit releases,
    so the caller must write its job and its upload count in that transaction.

    Raises:
        APIError 429 `fair_use_limit` (extras `metric`, `limit`, `used`, `resets_at`;
            `Retry-After`): the day's uploads are used.
        APIError 409 `storage_limit_reached` (extras `limit_mb`, `used_mb`, `adding_mb`): the
            file would pass the business's storage.
    """
    if tenant_id is None or not enabled:
        return
    from callobuzz_rag.fairuse.decision import resets_at
    from callobuzz_rag.fairuse.limits import effective_fair_use
    from callobuzz_rag.fairuse.usage import read_usage

    effective = await effective_fair_use(session, tenant_id)
    limits = effective.limits
    if effective.exempt or (limits.uploads_per_day is None and limits.storage_mb is None):
        return
    moment = _utc(now)
    wanted = [m for m in ("uploads_per_day", "storage_mb") if getattr(limits, m) is not None]
    # The tenant's lock (held until the caller's commit, where the job and the upload count
    # are written): parallel uploads read the numbers one after the other, not all at once.
    await lock_tenant_documents(session, tenant_id)
    usage = await read_usage(session, None, tenant_id, moment, metrics=wanted)
    if limits.uploads_per_day is not None and usage.uploads_per_day >= limits.uploads_per_day:
        reset = resets_at("uploads_per_day", moment)
        headers = (
            {"Retry-After": str(max(1, int((reset - moment).total_seconds())))} if reset else {}
        )
        raise APIError(
            429,
            "This workspace has uploaded as many files as it can today. "
            "You can upload more tomorrow.",
            "fair_use_limit",
            headers=headers,
            metric="uploads_per_day",
            limit=limits.uploads_per_day,
            used=usage.uploads_per_day,
            resets_at=_iso_z(reset) if reset else None,
        )
    if limits.storage_mb is not None:
        mb = 1024 * 1024
        if usage.storage_bytes + adding_bytes > limits.storage_mb * mb:
            raise APIError(
                409,
                "This file would take the workspace past its storage. Remove some documents "
                "or ask for more room.",
                "storage_limit_reached",
                limit_mb=limits.storage_mb,
                used_mb=-(-usage.storage_bytes // mb),
                adding_mb=-(-adding_bytes // mb),
            )


# /PIP3 storage


async def document_token_limit(
    session: "AsyncSession", corpus_id: uuid.UUID, engine_limit: int | None
) -> int | None:
    """Most tokens of parsed text one document may hold (None: no limit): the engine's
    `RAG_MAX_DOCUMENT_TOKENS`, or the corpus's tenant plan's lower `max_document_tokens`."""
    tenant_id = (
        await session.execute(select(Corpus.tenant_id).where(Corpus.id == corpus_id))
    ).scalar_one_or_none()
    plan_tokens = (
        (await effective_limits(session, tenant_id)).max_document_tokens
        if tenant_id is not None
        else None
    )
    if plan_tokens is None:
        return engine_limit
    return plan_tokens if engine_limit is None else min(engine_limit, plan_tokens)
