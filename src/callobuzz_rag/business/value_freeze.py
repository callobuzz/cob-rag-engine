"""Freeze closed months' value reports and e-mail last month's (TV4).

`freeze_and_send` runs hourly (beat). Every run freezes each closed month that has no row, back to
the month the workspace was created (at most `RETENTION_MONTHS`), so downtime on the 1st or a
workspace that was suspended then loses nothing: a frozen row is written once (`value_reports` is
unique on workspace and month). Only the latest closed month is e-mailed, and only in the first
`EMAIL_DAYS` days of a month in India.

The e-mail goes to each address once. The row is claimed with `FOR UPDATE SKIP LOCKED` while it is
queued (two overlapping runs never both send), the addresses already queued are kept in
`emailed_to`, and `emailed_at` is stamped when every recipient has been queued, so a queue failure
for one address is retried for that address alone on the next run. Each workspace is handled in
its own transaction; one failing workspace never stops the others.

Who gets nothing: workspaces that are not active, the deployer's own workspace and COB's system
workspace; a month with no conversations is frozen (the page shows zeros) but not e-mailed; the
deployer can switch the e-mail off (`trust.value_report_enabled`) and an owner can too
(`value_settings.email_enabled`).
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from callobuzz_rag.accounts.settings_store import EngineSettings, get_engine_settings
from callobuzz_rag.api.schemas.value import ValueReportOut
from callobuzz_rag.business import value_report as service
from callobuzz_rag.business.value import month_of, previous_month
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.value import ValueReport, ValueSettings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

EMAIL_DAYS = range(1, 8)  # days of the month, in India, on which last month's e-mail is sent
EMAIL_EXPIRES_S = 7 * 24 * 3600
Enqueue = Callable[..., None]


def _is_email_day(now: datetime) -> bool:
    return now.astimezone(ZoneInfo("Asia/Kolkata")).day in EMAIL_DAYS


def _closed_months(first: date, latest: date) -> list[date]:
    """The months to have a frozen report, newest first: `latest` back to `first`, never more
    than the retention."""
    months: list[date] = []
    month = latest
    while month >= first and len(months) < service.RETENTION_MONTHS:
        months.append(month)
        month = previous_month(month)
    return months


def claim_statement(tenant_id: uuid.UUID, month: date) -> Any:
    """The report row locked for the length of the transaction; a row another run holds is
    skipped, so overlapping runs never both queue its e-mail."""
    return (
        select(ValueReport)
        .where(ValueReport.tenant_id == tenant_id, ValueReport.month == month)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )


def email_context(
    report: ValueReportOut,
    *,
    business_name: str,
    product_name: str,
    dashboard_url: str,
) -> dict[str, Any]:
    """The `value_report` template's context. Money is formatted here, in whole units; the
    formula is the report's own wording."""
    from callobuzz_rag.accounts.email.render import dashboard_link

    cur = report.currency
    b, i = report.breakdown, report.inputs
    labels = {
        "leads": service.leads_label(i.leads_unrated),
        "handoffs": "Requests to talk to a person",
        "time": "Staff time saved",
    }
    month = date.fromisoformat(f"{report.month}-01")
    return {
        "business_name": business_name,
        "product_name": product_name,
        "month_label": month.strftime("%B %Y"),
        "total": service.format_value_money(b.total_minor, cur),
        "parts": [
            {
                "label": labels[line.key],
                "amount": service.format_value_money(line.amount_minor, cur),
                "formula": line.text,
            }
            for line in b.formula
        ],
        "conversations": i.conversations,
        "answered": i.answered,
        "after_hours": i.answered_after_hours,
        "leads_qualified": i.leads_qualified,
        "leads_label": service.leads_label(i.leads_unrated).lower(),
        "handoffs": i.handoffs,
        "bookings": i.bookings,
        "first_reply": service.format_first_reply(i.median_first_reply_ms) or "Not enough data",
        "top_questions": [{"question": q.question, "count": q.count} for q in i.top_questions],
        "unanswered": [{"question": q.question, "count": q.count} for q in i.unanswered],
        "report_url": dashboard_link(dashboard_url, f"value?month={report.month}"),
        "settings_url": dashboard_link(dashboard_url, "value?edit=1"),
        "saved_settings": report.saved_settings,
    }


async def _eligible_tenants(session: "AsyncSession") -> list[Any]:
    rows = await session.execute(
        select(Tenant.id, Tenant.name, Tenant.created_at).where(
            Tenant.status == "active",
            Tenant.is_operator.is_(False),
            Tenant.is_system.is_(False),
        )
    )
    return [SimpleNamespace(id=r[0], name=r[1], created_at=r[2]) for r in rows.all()]


async def _email_wanted(session: "AsyncSession", tenant_id: uuid.UUID) -> bool:
    row = (
        await session.execute(select(ValueSettings).where(ValueSettings.tenant_id == tenant_id))
    ).scalar_one_or_none()
    return True if row is None else bool(row.email_enabled)


async def _recipients(
    session: "AsyncSession", tenant_id: uuid.UUID, settings: EngineSettings
) -> list[str]:
    from callobuzz_rag.accounts.lifecycle import owner_emails

    to = await owner_emails(session, tenant_id)
    cc = settings.branding.support_email if settings.trust.value_report_cc_deployer else None
    if cc and str(cc) not in to:
        to = [*to, str(cc)]
    return to


async def _freeze(session: "AsyncSession", tenant: Any, month: date, ctx: Any) -> bool:
    """Write the frozen row of `month`; False when another run already did."""
    report = await service.build_month(session, tenant.id, month, ctx, live=False)
    session.add(
        ValueReport(
            tenant_id=tenant.id,
            month=month,
            data=report.model_dump(mode="json", exclude={"live"}),
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    return True


async def _one_workspace(
    session_factory: Any,
    dashboard_url: str,
    engine: EngineSettings,
    tenant: Any,
    now: datetime,
    enqueue: Enqueue,
) -> tuple[int, int]:
    """(frozen, emailed) for one workspace."""
    async with session_factory() as session:
        ctx = await service.workspace_context(session, tenant.id)
        latest = previous_month(month_of(now, ctx.timezone))
        first = (
            month_of(tenant.created_at, ctx.timezone) if tenant.created_at is not None else latest
        )
        wanted = _closed_months(first, latest)
        if not wanted:
            return 0, 0
        have = set(
            (
                await session.execute(
                    select(ValueReport.month).where(ValueReport.tenant_id == tenant.id)
                )
            )
            .scalars()
            .all()
        )
        frozen = 0
        for month in wanted:  # newest first: an old month that fails never blocks the latest
            if month in have:
                continue
            try:
                frozen += await _freeze(session, tenant, month, ctx)
            except Exception:
                if month == latest:
                    raise
                await session.rollback()
                logger.warning(
                    "Value report of %s for %s could not be frozen", tenant.id, month, exc_info=True
                )
        if not _is_email_day(now):
            return frozen, 0
        row = (await session.execute(claim_statement(tenant.id, latest))).scalar_one_or_none()
        if row is None or row.emailed_at is not None:
            return frozen, 0
        report = ValueReportOut.model_validate({**row.data, "live": False})
        if (
            report.inputs.conversations < 1
            or not engine.trust.value_report_enabled
            or not await _email_wanted(session, tenant.id)
        ):
            return frozen, 0
        recipients = await _recipients(session, tenant.id, engine)
        if not recipients:
            return frozen, 0
        context = email_context(
            report,
            business_name=tenant.name,
            product_name=engine.branding.product_name,
            dashboard_url=dashboard_url,
        )
        sent = [a.lower() for a in row.emailed_to or []]
        queued = 0
        missed = False
        for to in recipients:
            if to.lower() in sent:
                continue
            try:
                enqueue("value_report", to, context, expires_s=EMAIL_EXPIRES_S)
            except Exception:
                missed = True
                logger.error(
                    "Value report e-mail for %s could not be queued to one address",
                    tenant.id,
                    exc_info=True,
                )
                continue
            sent.append(to.lower())
            queued += 1
        row.emailed_to = sent
        if not missed:
            row.emailed_at = now
        await session.commit()
        return frozen, 1 if queued and not missed else 0


async def freeze_and_send(
    session_factory: Any,
    settings: Any,
    *,
    now: datetime | None = None,
    enqueue: Enqueue | None = None,
    engine_settings: EngineSettings | None = None,
) -> dict[str, int]:
    """Freeze every missing closed-month report and queue the latest one's e-mail; returns the
    counts."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    if enqueue is None:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        enqueue = enqueue_email
    engine = engine_settings or await get_engine_settings(session_factory)
    async with session_factory() as session:
        tenants = await _eligible_tenants(session)
    frozen = emailed = failed = 0
    for tenant in tenants:
        try:
            f, e = await _one_workspace(
                session_factory, settings.dashboard_url, engine, tenant, moment, enqueue
            )
        except Exception:
            failed += 1
            logger.warning("Value report for %s failed", tenant.id, exc_info=True)
            continue
        frozen += f
        emailed += e
    return {"frozen": frozen, "emailed": emailed, "failed": failed}
