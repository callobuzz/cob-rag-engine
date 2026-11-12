"""The value report of one workspace: settings, a month's numbers, frozen months, the printable
page (TV4).

`business/value.py` holds the formula and the SQL; this module joins them to the workspace's
own context (kind of business, time zone, currency), stores the owner's settings and freezes
closed months. A frozen month never changes: its numbers, the settings used and its wording are
stored in `value_reports.data` when it is frozen.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import cache
from typing import TYPE_CHECKING, Any, Final, Literal, cast
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from callobuzz_rag.accounts.settings_store import Branding, powered_text_for
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.home import HomeValue, HomeValueMonth
from callobuzz_rag.api.schemas.value import (
    BreakdownOut,
    CountedQuestion,
    FormulaLineOut,
    ValueInputsOut,
    ValueReportOut,
    ValueSettingsOut,
    ValueSettingsValues,
)
from callobuzz_rag.billing.money import Money
from callobuzz_rag.business.value import (
    DEFAULT_TIMEZONE,
    ValueBreakdown,
    ValueInputs,
    ValueSettingsIn,
    estimate,
    fetch_inputs,
    month_bounds,
    month_of,
    previous_month,
    valid_timezone,
)
from callobuzz_rag.business.value_defaults import suggested_for
from callobuzz_rag.db.models.assistant_config import (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    AssistantConfigVersion,
)
from callobuzz_rag.db.models.assistant_turn import AssistantTurn
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.value import ValueReport, ValueSettings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

CurrencyCode = Literal["INR", "USD"]
TIMES: Final = "×"  # noqa: RUF001
FALLBACK_TYPE: Final = "generic_support"
# How many closed months are frozen and kept: the report list shows 24, the daily clean-up
# deletes older ones.
RETENTION_MONTHS: Final = 24
VALUE_FIELDS: Final = (
    "lead_value_minor",
    "handoff_value_minor",
    "staff_hour_minor",
    "minutes_per_answer",
)


@dataclass(frozen=True)
class WorkspaceContext:
    business_type: str
    timezone: str
    currency: CurrencyCode


@dataclass(frozen=True)
class SettingsState:
    values: ValueSettingsIn
    saved: bool
    currency: CurrencyCode
    email_enabled: bool
    industry_defaults: str | None
    business_type: str


# ------------------------------------------------------------------ words and money


def format_value_money(minor: int, currency: str) -> str:
    """`₹13,500` (lakh grouping for rupees), `₹5,150.83`, `$15`: whole units when there are no
    paise."""
    shown = Money(minor, "USD" if currency == "USD" else "INR").format()
    return shown[:-3] if shown.endswith(".00") else shown


def format_duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} h {rest} min"
    return f"{hours} h" if hours else f"{rest} min"


def format_first_reply(ms: int | None) -> str | None:
    if ms is None:
        return None
    if ms < 1000:
        return "under a second"
    seconds = round(ms / 1000)
    return f"{seconds} second{'' if seconds == 1 else 's'}"


def _plural(count: int, one: str, many: str) -> str:
    return f"{count:,} {one if count == 1 else many}"


def leads_word(unrated: int) -> str:
    """`lead` when some of the counted leads were never rated (the knowledge base had no
    qualification rules), `qualified lead` when every one met a rule."""
    return "lead" if unrated > 0 else "qualified lead"


def leads_label(unrated: int) -> str:
    return "Leads" if unrated > 0 else "Qualified leads"


def formula_lines(
    inputs: ValueInputs, settings: ValueSettingsIn, breakdown: ValueBreakdown, currency: str
) -> list[FormulaLineOut]:
    def money(minor: int) -> str:
        return format_value_money(minor, currency)

    word = leads_word(inputs.leads_unrated)
    texts = {
        "leads": (
            f"{_plural(inputs.leads_qualified, word, word + 's')} "
            f"{TIMES} {money(settings.lead_value_minor)} = {money(breakdown.leads_minor)}"
        ),
        "handoffs": (
            f"{_plural(inputs.handoffs, 'request to talk to a person', 'requests to talk to a person')} "
            f"{TIMES} {money(settings.handoff_value_minor)} = {money(breakdown.handoffs_minor)}"
        ),
        "time": (
            f"{_plural(inputs.answered, 'question answered', 'questions answered')} "
            f"{TIMES} {settings.minutes_per_answer} min saved = "
            f"{format_duration(breakdown.minutes_saved)} {TIMES} "
            f"{money(settings.staff_hour_minor)}/h = {money(breakdown.time_minor)}"
        ),
    }
    return [
        FormulaLineOut(
            key=cast("Literal['leads', 'handoffs', 'time']", line.key),
            inputs=line.inputs,
            amount_minor=line.amount_minor,
            text=texts[line.key],
        )
        for line in breakdown.formula
    ]


# ------------------------------------------------------------------ the workspace


async def workspace_context(session: "AsyncSession", tenant_id: uuid.UUID) -> WorkspaceContext:
    """The kind of business and time zone of the main knowledge base's assistant, and the
    workspace's billing currency (its account, else its plan's pricing, else the engine
    default)."""
    from callobuzz_rag.accounts.settings_store import read_engine_settings_row
    from callobuzz_rag.business.home import main_corpus
    from callobuzz_rag.conversation.schema import load_config

    business_type, timezone = FALLBACK_TYPE, DEFAULT_TIMEZONE
    corpus = await main_corpus(session, tenant_id)
    if corpus is not None:
        versions = (
            (
                await session.execute(
                    select(AssistantConfigVersion).where(
                        AssistantConfigVersion.corpus_id == corpus.id,
                        AssistantConfigVersion.status.in_([STATUS_PUBLISHED, STATUS_DRAFT]),
                    )
                )
            )
            .scalars()
            .all()
        )
        shown = next((v for v in versions if v.status == STATUS_PUBLISHED), None) or next(
            (v for v in versions if v.status == STATUS_DRAFT), None
        )
        if shown is not None:
            try:
                config = load_config(shown.config)
                business_type = config.identity.business_type
                timezone = valid_timezone(config.identity.card.timezone)
            except Exception:
                logger.warning("Value: assistant config of %s unreadable", corpus.id, exc_info=True)

    currency: str | None = None
    account = (
        await session.execute(
            select(BillingAccount.currency).where(BillingAccount.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if account is not None:
        currency = account
    else:
        tenant = await session.get(Tenant, tenant_id)
        plan_id = getattr(tenant, "plan_id", None)
        if plan_id is not None:
            currency = (
                await session.execute(
                    select(PlanPricing.currency).where(
                        PlanPricing.plan_id == plan_id, PlanPricing.is_current.is_(True)
                    )
                )
            ).scalar_one_or_none()
    if currency not in ("INR", "USD"):
        currency = (await read_engine_settings_row(session))[0].billing.default_currency
    return WorkspaceContext(business_type, timezone, cast("CurrencyCode", currency))


# ------------------------------------------------------------------ settings


async def settings_state(
    session: "AsyncSession", tenant_id: uuid.UUID, ctx: WorkspaceContext
) -> SettingsState:
    """The saved settings, or the suggestions for this kind of business when none are saved."""
    row = await session.get(ValueSettings, tenant_id)
    if row is None:
        return SettingsState(
            values=suggested_for(ctx.business_type, ctx.currency),
            saved=False,
            currency=ctx.currency,
            email_enabled=True,
            industry_defaults=None,
            business_type=ctx.business_type,
        )
    return SettingsState(
        values=ValueSettingsIn(
            lead_value_minor=row.lead_value_minor,
            handoff_value_minor=row.handoff_value_minor,
            staff_hour_minor=row.staff_hour_minor,
            minutes_per_answer=row.minutes_per_answer,
        ),
        saved=True,
        currency=cast("CurrencyCode", row.currency),
        email_enabled=row.email_enabled,
        industry_defaults=row.industry_defaults,
        business_type=ctx.business_type,
    )


async def home_value(
    session: "AsyncSession", tenant_id: uuid.UUID, timezone: str, now: datetime
) -> HomeValue:
    """Month to date and last month's frozen total, for the Home card. With no saved settings
    the suggestions for the workspace's kind of business are used (and flagged)."""
    row = await session.get(ValueSettings, tenant_id)
    if row is not None:
        state = await settings_state(
            session, tenant_id, WorkspaceContext(FALLBACK_TYPE, timezone, "INR")
        )
    else:
        state = await settings_state(
            session, tenant_id, await workspace_context(session, tenant_id)
        )
    month = month_of(now, timezone)
    start, end = month_bounds(month, timezone)
    inputs = await fetch_inputs(session, tenant_id, start, min(end, now))
    breakdown = estimate(inputs, state.values)
    last = await frozen_total(session, tenant_id, previous_month(month))
    return HomeValue(
        month_to_date=HomeValueMonth(
            total_minor=breakdown.total_minor,
            currency=state.currency,
            answered=inputs.answered,
            leads_qualified=inputs.leads_qualified,
            handoffs=inputs.handoffs,
        ),
        saved_settings=state.saved,
        last_month_total_minor=last,
    )


def settings_out(state: SettingsState) -> ValueSettingsOut:
    suggestion = suggested_for(state.business_type, state.currency)
    return ValueSettingsOut(
        saved=state.saved,
        currency=state.currency,
        email_enabled=state.email_enabled,
        industry_defaults=state.industry_defaults,
        business_type=state.business_type,
        suggested=_values(suggestion),
        **_values(state.values).model_dump(),
    )


def _values(s: ValueSettingsIn) -> ValueSettingsValues:
    return ValueSettingsValues(
        lead_value_minor=s.lead_value_minor,
        handoff_value_minor=s.handoff_value_minor,
        staff_hour_minor=s.staff_hour_minor,
        minutes_per_answer=s.minutes_per_answer,
    )


async def save_settings(
    session: "AsyncSession",
    tenant_id: uuid.UUID,
    ctx: WorkspaceContext,
    new: dict[str, Any],
    *,
    actor_label: str,
) -> list[str]:
    """Create or update the workspace's row; returns the fields that changed (the audit's
    `changed`). A first save names the four money and minute fields whatever their value. The
    caller records the audit row and commits."""
    row = await session.get(ValueSettings, tenant_id, with_for_update=True)
    if row is None:
        # No row to lock yet, so two first saves (a double click) can both get here: the
        # insert runs in a savepoint, and the one that loses the primary key race applies its
        # values to the winner's row as an update instead of failing.
        row = ValueSettings(
            tenant_id=tenant_id,
            currency=ctx.currency,
            lead_value_minor=new["lead_value_minor"],
            handoff_value_minor=new["handoff_value_minor"],
            staff_hour_minor=new["staff_hour_minor"],
            minutes_per_answer=new["minutes_per_answer"],
            email_enabled=new["email_enabled"],
            industry_defaults=new.get("industry_defaults"),
            updated_by=actor_label[:320],
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            row = (
                await session.execute(
                    select(ValueSettings)
                    .where(ValueSettings.tenant_id == tenant_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
        else:
            changed = list(VALUE_FIELDS)
            if not new["email_enabled"]:
                changed.append("email_enabled")
            if new.get("industry_defaults"):
                changed.append("industry_defaults")
            return changed
    changed = []
    for name in (*VALUE_FIELDS, "email_enabled", "industry_defaults"):
        value = new.get(name)
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed.append(name)
    if changed:
        row.updated_by = actor_label[:320]
    return changed


# ------------------------------------------------------------------ the numbers


async def after_hours_since(session: "AsyncSession", tenant_id: uuid.UUID, tz: str) -> date | None:
    """The first day (business time) a turn recorded whether the business was open. Earlier
    turns count as unknown, so a report before it must say so."""
    first = (
        await session.execute(
            select(func.min(AssistantTurn.created_at)).where(
                AssistantTurn.tenant_id == tenant_id, AssistantTurn.office_open.is_not(None)
            )
        )
    ).scalar_one_or_none()
    if first is None:
        return None
    return first.astimezone(ZoneInfo(valid_timezone(tz))).date()


def build_report_out(
    *,
    month: date,
    live: bool,
    currency: str,
    state: SettingsState,
    inputs: ValueInputs,
    counting_since: date | None,
) -> ValueReportOut:
    breakdown = estimate(inputs, state.values)
    lines = formula_lines(inputs, state.values, breakdown, currency)
    since = counting_since if counting_since is not None and counting_since > month else None
    return ValueReportOut(
        month=f"{month:%Y-%m}",
        live=live,
        currency="USD" if currency == "USD" else "INR",
        saved_settings=state.saved,
        settings_used=_values(state.values),
        inputs=ValueInputsOut(
            conversations=inputs.conversations,
            questions=inputs.questions,
            answered=inputs.answered,
            answered_after_hours=inputs.answered_after_hours,
            leads_total=inputs.leads_total,
            leads_qualified=inputs.leads_qualified,
            leads_unrated=inputs.leads_unrated,
            leads_partial=inputs.leads_partial,
            handoffs=inputs.handoffs,
            bookings=inputs.bookings,
            median_first_reply_ms=inputs.median_first_reply_ms,
            top_questions=[CountedQuestion(question=q, count=n) for q, n in inputs.top_questions],
            unanswered=[CountedQuestion(question=q, count=n) for q, n in inputs.unanswered],
        ),
        breakdown=BreakdownOut(
            leads_minor=breakdown.leads_minor,
            handoffs_minor=breakdown.handoffs_minor,
            minutes_saved=breakdown.minutes_saved,
            time_minor=breakdown.time_minor,
            total_minor=breakdown.total_minor,
            formula=lines,
        ),
        formula_text=[line.text for line in lines],
        after_hours_since=since.isoformat() if since is not None else None,
    )


async def build_month(
    session: "AsyncSession",
    tenant_id: uuid.UUID,
    month: date,
    ctx: WorkspaceContext,
    *,
    live: bool,
    state: SettingsState | None = None,
    now: datetime | None = None,
) -> ValueReportOut:
    """The report of `month` built from the rows, with the saved settings (or suggestions)."""
    state = state or await settings_state(session, tenant_id, ctx)
    start, end = month_bounds(month, ctx.timezone)
    if live and now is not None:
        end = min(end, now)  # month to date
    inputs = await fetch_inputs(session, tenant_id, start, end)
    return build_report_out(
        month=month,
        live=live,
        currency=state.currency,
        state=state,
        inputs=inputs,
        counting_since=await after_hours_since(session, tenant_id, ctx.timezone),
    )


def parse_month(text: str) -> date:
    """`YYYY-MM` -> the 1st of that month; 422 `value_month_invalid` otherwise."""
    try:
        year, month = text.split("-")
        if len(year) != 4 or len(month) != 2:
            raise ValueError
        return date(int(year), int(month), 1)
    except ValueError:
        raise month_invalid("Use a month like 2026-10.") from None


def month_invalid(detail: str) -> APIError:
    return APIError(422, detail, "value_month_invalid")


async def report_for(
    session: "AsyncSession",
    tenant_id: uuid.UUID,
    month_text: str | None,
    *,
    now: datetime | None = None,
) -> ValueReportOut:
    """The report of a month: a closed month from its frozen row (built on the fly, unsaved,
    until the 1st's job freezes it), the current month live."""
    now = now or datetime.now(UTC)
    ctx = await workspace_context(session, tenant_id)
    current = month_of(now, ctx.timezone)
    month = parse_month(month_text) if month_text else current
    tenant = await session.get(Tenant, tenant_id)
    created = getattr(tenant, "created_at", None)
    if month > current:
        raise month_invalid("That month has not happened yet.")
    if created is not None and month < month_of(created, ctx.timezone):
        raise month_invalid("This workspace did not exist in that month.")
    if month == current:
        return await build_month(session, tenant_id, month, ctx, live=True, now=now)
    frozen = await frozen_report(session, tenant_id, month)
    if frozen is not None:
        return frozen
    return await build_month(session, tenant_id, month, ctx, live=False)


async def frozen_total(session: "AsyncSession", tenant_id: uuid.UUID, month: date) -> int | None:
    """The estimated total of a frozen month, or None when it is not frozen."""
    row = (
        await session.execute(
            select(ValueReport).where(
                ValueReport.tenant_id == tenant_id, ValueReport.month == month
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        return int(row.data["breakdown"]["total_minor"])
    except (KeyError, TypeError, ValueError):
        return None


async def frozen_report(
    session: "AsyncSession", tenant_id: uuid.UUID, month: date
) -> ValueReportOut | None:
    row = (
        await session.execute(
            select(ValueReport).where(
                ValueReport.tenant_id == tenant_id, ValueReport.month == month
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return ValueReportOut.model_validate({**row.data, "live": False})


# ------------------------------------------------------------------ the printable page


@cache
def _env() -> Environment:
    return Environment(
        loader=PackageLoader("callobuzz_rag.billing", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html(report: ValueReportOut, workspace: str, branding: Branding) -> str:
    """A standalone printable page (A4 print stylesheet; the browser's print dialog saves it
    as a PDF). Every value reaches the template as text, autoescaped."""
    cur = report.currency

    def money(minor: int) -> str:
        return format_value_money(minor, cur)

    inputs = report.inputs
    month_label = date.fromisoformat(f"{report.month}-01").strftime("%B %Y")
    counts = [
        ("Conversations", f"{inputs.conversations:,}"),
        ("Questions answered without staff", f"{inputs.answered:,}"),
        ("Answered after hours", f"{inputs.answered_after_hours:,}"),
        (leads_label(inputs.leads_unrated), f"{inputs.leads_qualified:,}"),
        ("Partial leads", f"{inputs.leads_partial:,}"),
        ("Requests to talk to a person", f"{inputs.handoffs:,}"),
        ("Bookings requested", f"{inputs.bookings:,}"),
        ("First reply", format_first_reply(inputs.median_first_reply_ms) or "Not enough data"),
    ]
    return (
        _env()
        .get_template("value_report.html")
        .render(
            product=branding.product_name,
            logo_url=branding.logo_url,
            color=branding.primary_color,
            workspace=workspace,
            month_label=month_label,
            live=report.live,
            total=money(report.breakdown.total_minor),
            lines=report.formula_text,
            counts=counts,
            top_questions=[(q.question, q.count) for q in report.inputs.top_questions],
            unanswered=[(q.question, q.count) for q in report.inputs.unanswered],
            saved_settings=report.saved_settings,
            after_hours_since=report.after_hours_since,
            powered_text=powered_text_for(branding),
        )
    )
