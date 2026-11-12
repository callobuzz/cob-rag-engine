"""Monthly statements: what a workspace's wallet did in one UTC calendar month (BIL6).

A statement is built from three things and adds up to the ledger by construction:

* the month's ledger entries (by their `period`), each a line; the many small posted usage
  batches are folded into one "Usage" line so a month stays readable;
* the usage breakdown (knowledge base, model, source) from the usage charges *posted* in the
  month, in micro units, plus a "rounding carried" line that is the posting remainder at the
  start of the month minus the one at the end, so `breakdown + carried == usage total` exactly;
* the opening balance, the sum of every entry of an earlier period.

Closed months are frozen in `billing_statements` by a beat on the 1st (idempotent; the unique
`(account_id, month)` makes a second run write nothing); the current month is built live and
marked "so far". The pure half (`build_statement`) takes plain rows so the property tests need
no database. The customer never sees the markup or what the operator's AI cost: a usage line is
what was charged, nothing else.
"""

import csv
import io
import logging
import re
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from functools import cache
from typing import Any, Final, cast

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select
from sqlalchemy.dialects.postgresql import insert

from callobuzz_rag.accounts.settings_store import Branding, powered_text_for
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.ledger import EntryType
from callobuzz_rag.billing.money import MICRO_PER_MINOR, MINUS, PPM, Currency, Money
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.billing_statement import BillingStatement
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.usage_charge import UsageCharge
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from callobuzz_rag.db.models.widget_key import WidgetKey

logger = logging.getLogger(__name__)

DELETED_KB: Final = "Deleted knowledge base"
COB_LABEL: Final = "COB"
WIDGET_PREFIX: Final = "widget:"

# (key, title, the entry types it holds), in the order a statement lists them
SECTIONS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("subscription", "Subscription", (EntryType.SUBSCRIPTION_CHARGE.value,)),
    ("usage", "Usage", (EntryType.USAGE_CHARGE.value,)),
    ("add_ons", "Add-ons", (EntryType.ADD_ON_PURCHASE.value,)),
    ("top_ups", "Top-ups", (EntryType.TOP_UP.value,)),
    ("refunds", "Refunds", (EntryType.REFUND.value,)),
    ("adjustments", "Adjustments", (EntryType.ADJUSTMENT.value,)),
    ("included_credit", "Included credit", (EntryType.INCLUDED_CREDIT.value,)),
    ("expiry", "Expired credit", (EntryType.EXPIRY.value,)),
)

SOURCE_LABELS: Final[Mapping[str, str]] = {
    "api": "API",
    "stream": "API (streamed)",
    "conversation": "Chat",
    "cob": COB_LABEL,
    "eval": "Test runs",
    "setup": "Setup",
    "assistant": "Assistant",
    "lead_summary": "Lead summaries",
    "handoff_summary": "Handoff summaries",
    "whatsapp": "WhatsApp",  # TV6
    "whatsapp_fee": "WhatsApp messages",  # TV6: Meta's per-message fees, passed through
}

STORED_SECTION_KEYS: Final = tuple(key for key, _, _ in SECTIONS)


# ------------------------------------------------------------------ the document


class StatementLine(BaseModel):
    date: date
    entry_type: str
    description: str
    amount_minor: int


class StatementSection(BaseModel):
    key: str
    title: str
    total_minor: int
    lines: list[StatementLine]


class UsageBreakdownRow(BaseModel):
    label: str  # "Clinic FAQ · gpt-4o-mini · Website widget (key …a1f2)"
    knowledge_base: str
    model: str
    source: str
    answers: int
    amount_micro: int  # account-currency micro units: a fraction of a paisa is not rounded away


class StatementData(BaseModel):
    """The whole statement. `lines` are what moved the balance (their sum is
    `closing - opening`); `usage_breakdown` explains the Usage line."""

    month: date  # the first day of the UTC month
    currency: Currency
    opening_minor: int
    closing_minor: int
    live: bool = False  # the current month, "so far"
    lines: list[StatementLine] = Field(default_factory=list)
    sections: list[StatementSection] = Field(default_factory=list)
    totals: dict[str, int] = Field(default_factory=dict)  # signed, by entry type
    usage_breakdown: list[UsageBreakdownRow] = Field(default_factory=list)
    rounding_carried_micro: int = 0
    unpriced_count: int = 0
    answers: int = 0
    notes: list[str] = Field(default_factory=list)
    generated_at: datetime


@dataclass(frozen=True)
class ChargeGroup:
    """Usage charges posted in the month that share a knowledge base, source and model."""

    corpus_id: uuid.UUID | None
    source: str
    model: str
    answers: int
    amount_micro: int
    unpriced: int = 0  # cost_plus calls with no known price (charged nothing)
    fx_ppm: int = 0  # the fixed rate snapshotted on priced cost_plus charges, 0 otherwise


@dataclass(frozen=True)
class Names:
    """What the statement calls things, as they are named when it is generated."""

    corpora: Mapping[uuid.UUID, str] = field(default_factory=dict)
    widget_keys: Mapping[str, str] = field(default_factory=dict)  # key id -> last four of the key


# ------------------------------------------------------------------ months


def month_of(moment: datetime | date) -> date:
    day = moment.astimezone(UTC).date() if isinstance(moment, datetime) else moment
    return day.replace(day=1)


def next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def previous_month(month: date) -> date:
    return date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1)


def month_range(first: date, last: date) -> list[date]:
    """Every first-of-month from `first` to `last`, oldest first."""
    out: list[date] = []
    cursor = first
    while cursor <= last:
        out.append(cursor)
        cursor = next_month(cursor)
    return out


def _start(month: date) -> datetime:
    return datetime.combine(month, time.min, tzinfo=UTC)


# ------------------------------------------------------------------ labels


def source_label(source: str, names: Names) -> str:
    if source.startswith(WIDGET_PREFIX):
        last4 = names.widget_keys.get(source[len(WIDGET_PREFIX) :])
        return f"Website widget (key …{last4})" if last4 else "Website widget"
    return SOURCE_LABELS.get(source, source)


def kb_label(corpus_id: uuid.UUID | None, names: Names) -> str:
    if corpus_id is None:
        return DELETED_KB
    return names.corpora.get(corpus_id, DELETED_KB)


def _rate(fx_ppm: int) -> str:
    """`83.50` from 83_500_000 ppm, integer math, rounded half up to cents."""
    cents = (fx_ppm * 100 + PPM // 2) // PPM
    return f"{cents // 100}.{cents % 100:02d}"


def format_micro(micro: int, currency: Currency) -> str:
    """An amount in micro units with four decimals (`₹0.0168`), for the usage breakdown."""
    sign = MINUS if micro < 0 else ""
    hundredths = (abs(micro) + 50) // 100  # units of 1e-4 of the major unit, rounded half up
    whole, frac = divmod(hundredths, 10_000)
    base = Money(whole * 100, currency).format()  # "₹1,234.00"
    return f"{sign}{base[:-3]}.{frac:04d}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ------------------------------------------------------------------ the pure builder


def build_statement(
    entries: Iterable[Any],
    charges: Iterable[ChargeGroup],
    *,
    opening: int,
    residue_start: int,
    residue_end: int,
    names: Names,
    month: date,
    currency: Currency = "INR",
    live: bool = False,
    now: datetime | None = None,
) -> StatementData:
    """The statement of `month` from its ledger entries (objects with `seq`, `entry_type`,
    `amount_minor`, `memo_public`, `created_at`) and its posted usage charge groups.

    `opening` is the balance before the month (the sum of every earlier entry);
    `residue_start` and `residue_end` are the posting remainders at the month's edges."""
    rows = sorted(entries, key=lambda e: e.seq)
    totals = {t.value: 0 for t in EntryType}
    lines: list[StatementLine] = []
    usage_last: datetime | None = None
    for row in rows:
        kind = str(row.entry_type)
        totals[kind] = totals.get(kind, 0) + int(row.amount_minor)
        if kind == EntryType.USAGE_CHARGE.value:
            usage_last = row.created_at
            continue
        lines.append(
            StatementLine(
                date=row.created_at.astimezone(UTC).date(),
                entry_type=kind,
                description=(row.memo_public or "").strip() or kind.replace("_", " ").title(),
                amount_minor=int(row.amount_minor),
            )
        )

    merged: dict[tuple[uuid.UUID | None, str, str], list[int]] = defaultdict(lambda: [0, 0])
    unpriced = 0
    rates: set[int] = set()
    for group in charges:
        slot = merged[(group.corpus_id, group.source, group.model)]
        slot[0] += group.answers
        slot[1] += group.amount_micro
        unpriced += group.unpriced
        if group.fx_ppm:
            rates.add(group.fx_ppm)
    answers = sum(slot[0] for slot in merged.values())

    usage_total = totals[EntryType.USAGE_CHARGE.value]
    if usage_total and usage_last is not None:
        lines.append(
            StatementLine(
                date=usage_last.astimezone(UTC).date(),
                entry_type=EntryType.USAGE_CHARGE.value,
                description=f"Usage: {_plural(answers, 'answer')}" if answers else "Usage",
                amount_minor=usage_total,
            )
        )
    lines.sort(key=lambda line: line.date)  # stable: entries keep their order within a day

    breakdown: list[UsageBreakdownRow] = []
    for (corpus_id, source, model), (n_answers, amount) in merged.items():
        kb = kb_label(corpus_id, names)
        src = source_label(source, names)
        parts = [kb, model] if model else [kb]
        if src != kb:
            parts.append(src)
        breakdown.append(
            UsageBreakdownRow(
                label=" · ".join(parts),
                knowledge_base=kb,
                model=model,
                source=src,
                answers=n_answers,
                amount_micro=amount,
            )
        )
    breakdown.sort(key=lambda r: (-r.amount_micro, r.label))

    sections: list[StatementSection] = []
    for key, title, kinds in SECTIONS:
        chosen = [line for line in lines if line.entry_type in kinds]
        if chosen:
            sections.append(
                StatementSection(
                    key=key,
                    title=title,
                    total_minor=sum(line.amount_minor for line in chosen),
                    lines=chosen,
                )
            )

    notes: list[str] = []
    if unpriced:
        notes.append(
            f"{_plural(unpriced, 'AI call')} had no known price and "
            f"{'was' if unpriced == 1 else 'were'} charged nothing (price unknown)."
        )
    if rates and currency == "INR":
        low, high = min(rates), max(rates)
        shown = f"₹{_rate(low)}" if low == high else f"₹{_rate(low)} to ₹{_rate(high)}"
        notes.append(f"Usage priced at AI cost is converted at {shown} per US$1.")

    return StatementData(
        month=month,
        currency=currency,
        opening_minor=opening,
        closing_minor=opening + sum(line.amount_minor for line in lines),
        live=live,
        lines=lines,
        sections=sections,
        totals=totals,
        usage_breakdown=breakdown,
        rounding_carried_micro=residue_start - residue_end,
        unpriced_count=unpriced,
        answers=answers,
        notes=notes,
        generated_at=now or datetime.now(UTC),
    )


# ------------------------------------------------------------------ the database side


def not_found() -> APIError:
    return APIError(404, "There is no statement for that month.", "statement_not_found")


async def first_month(session: Any, account: BillingAccount) -> date:
    """The earliest month with anything to say: the account's creation or its first entry's
    period (a subscription catch-up can start before the account row)."""
    earliest = (
        await session.execute(
            select(func.min(WalletLedger.period)).where(WalletLedger.account_id == account.id)
        )
    ).scalar_one()
    created = month_of(account.created_at) if account.created_at is not None else None
    options = [m for m in (earliest, created) if m is not None]
    return min(options) if options else month_of(datetime.now(UTC))


async def available_months(
    session: Any, account: BillingAccount, now: datetime | None = None
) -> list[date]:
    """Every month from the first to the current one, oldest first."""
    current = month_of(now or datetime.now(UTC))
    return month_range(min(await first_month(session, account), current), current)


async def _entries(session: Any, account: BillingAccount, month: date) -> list[WalletLedger]:
    return list(
        (
            await session.execute(
                select(WalletLedger)
                .where(WalletLedger.account_id == account.id, WalletLedger.period == month)
                .order_by(WalletLedger.seq)
            )
        )
        .scalars()
        .all()
    )


async def _balance_before(session: Any, account: BillingAccount, month: date) -> int:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(WalletLedger.amount_minor), 0)).where(
                WalletLedger.account_id == account.id, WalletLedger.period < month
            )
        )
    ).scalar_one()
    return int(total)


async def _residue_at(session: Any, account: BillingAccount, boundary: date) -> int:
    """The posting remainder just before `boundary`: every charge posted so far, minus what the
    ledger took of it (the remainder starts at 0 and only posting changes it)."""
    posted = (
        await session.execute(
            select(func.coalesce(func.sum(UsageCharge.amount_micro), 0)).where(
                UsageCharge.account_id == account.id,
                UsageCharge.currency == account.currency,
                UsageCharge.posted_at.is_not(None),
                UsageCharge.posted_at < _start(boundary),
            )
        )
    ).scalar_one()
    taken = (
        await session.execute(
            select(func.coalesce(-func.sum(WalletLedger.amount_minor), 0)).where(
                WalletLedger.account_id == account.id,
                WalletLedger.entry_type == EntryType.USAGE_CHARGE.value,
                WalletLedger.period < boundary,
            )
        )
    ).scalar_one()
    return int(posted) - int(taken) * MICRO_PER_MINOR


async def _charge_groups(session: Any, account: BillingAccount, month: date) -> list[ChargeGroup]:
    cost_plus = UsageCharge.usage_mode == "cost_plus"
    fx = case((and_(cost_plus, UsageCharge.amount_micro > 0), UsageCharge.fx_ppm), else_=0)
    rows = (
        await session.execute(
            select(
                UsageCharge.corpus_id,
                UsageCharge.source,
                UsageCharge.model,
                fx.label("fx"),
                func.coalesce(func.sum(UsageCharge.questions), 0),
                func.coalesce(func.sum(UsageCharge.amount_micro), 0),
                func.count().filter(and_(cost_plus, UsageCharge.cost_known.is_(False))),
            )
            .where(
                UsageCharge.account_id == account.id,
                UsageCharge.currency == account.currency,
                UsageCharge.posted_at >= _start(month),
                UsageCharge.posted_at < _start(next_month(month)),
                UsageCharge.waived.is_(None),
            )
            .group_by(UsageCharge.corpus_id, UsageCharge.source, UsageCharge.model, fx)
        )
    ).all()
    return [
        ChargeGroup(
            corpus_id=r[0],
            source=r[1],
            model=r[2] or "",
            answers=int(r[4]),
            amount_micro=int(r[5]),
            unpriced=int(r[6]),
            fx_ppm=int(r[3]),
        )
        for r in rows
    ]


async def _names(session: Any, groups: Sequence[ChargeGroup]) -> Names:
    corpus_ids = {g.corpus_id for g in groups if g.corpus_id is not None}
    key_ids = {
        uuid.UUID(g.source[len(WIDGET_PREFIX) :])
        for g in groups
        if g.source.startswith(WIDGET_PREFIX) and _is_uuid(g.source[len(WIDGET_PREFIX) :])
    }
    corpora: dict[uuid.UUID, str] = {}
    if corpus_ids:
        for cid, name, is_system in (
            await session.execute(
                select(Corpus.id, Corpus.name, Corpus.is_system).where(Corpus.id.in_(corpus_ids))
            )
        ).all():
            corpora[cid] = COB_LABEL if is_system else name
    widgets: dict[str, str] = {}
    if key_ids:
        for kid, public_key in (
            await session.execute(
                select(WidgetKey.id, WidgetKey.public_key).where(WidgetKey.id.in_(key_ids))
            )
        ).all():
            widgets[str(kid)] = public_key[-4:]
    return Names(corpora, widgets)


def _is_uuid(text: str) -> bool:
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return True


async def build_month(
    session: Any,
    account: BillingAccount,
    month: date,
    *,
    now: datetime | None = None,
    live: bool = False,
) -> StatementData:
    """Read the month's rows and build its statement (never stored here)."""
    moment = now or datetime.now(UTC)
    entries = await _entries(session, account, month)
    groups = await _charge_groups(session, account, month)
    return build_statement(
        entries,
        groups,
        opening=await _balance_before(session, account, month),
        residue_start=await _residue_at(session, account, month),
        residue_end=await _residue_at(session, account, next_month(month)),
        names=await _names(session, groups),
        month=month,
        currency=cast("Currency", account.currency),
        live=live,
        now=moment,
    )


async def statement_for(
    session: Any, account: BillingAccount, month: date, *, now: datetime | None = None
) -> StatementData:
    """A month's statement: the frozen one when the month is closed and was snapshotted, else
    built from the ledger (the current month is marked live).

    Raises:
        APIError: 404 `statement_not_found` for a month before the account's first month or in
            the future.
    """
    moment = now or datetime.now(UTC)
    current = month_of(moment)
    if month > current or month < min(await first_month(session, account), current):
        raise not_found()
    if month < current:
        stored = (
            await session.execute(
                select(BillingStatement).where(
                    BillingStatement.account_id == account.id, BillingStatement.month == month
                )
            )
        ).scalar_one_or_none()
        if stored is not None:
            return StatementData.model_validate(stored.data)
    return await build_month(session, account, month, now=moment, live=month == current)


@dataclass(frozen=True)
class MonthSummary:
    month: date
    opening_minor: int
    closing_minor: int
    live: bool


async def summaries(
    session: Any, account: BillingAccount, *, now: datetime | None = None, limit: int | None = None
) -> list[MonthSummary]:
    """Opening and closing balance for every month, newest first, from one grouped query (a
    statement's closing is the running sum of the entries of its period and earlier)."""
    moment = now or datetime.now(UTC)
    current = month_of(moment)
    per_month = {
        period: int(total)
        for period, total in (
            await session.execute(
                select(WalletLedger.period, func.sum(WalletLedger.amount_minor))
                .where(WalletLedger.account_id == account.id)
                .group_by(WalletLedger.period)
            )
        ).all()
    }
    first = min(await first_month(session, account), current)
    running = sum(total for period, total in per_month.items() if period < first)
    out: list[MonthSummary] = []
    for month in month_range(first, current):
        opening = running
        running += per_month.get(month, 0)
        out.append(MonthSummary(month, opening, running, month == current))
    out.reverse()
    return out[:limit] if limit else out


# ------------------------------------------------------------------ the monthly snapshot

BuildMonth = Callable[..., Awaitable[StatementData]]
MonthsFor = Callable[..., Awaitable[list[date]]]


async def snapshot_closed_months(
    session_factory: Any,
    *,
    now: datetime | None = None,
    build: BuildMonth | None = None,
    months_for: MonthsFor | None = None,
) -> int:
    """Freeze every closed month that has no statement yet; returns how many were written.

    Idempotent: a month that already has a statement is skipped, and the unique
    `(account_id, month)` makes a racing second run write nothing. One transaction per account;
    a failing account is logged and the others go on."""
    moment = now or datetime.now(UTC)
    current = month_of(moment)
    builder = build or build_month
    lister = months_for or available_months
    async with session_factory() as session:
        account_ids = list(
            (await session.execute(select(BillingAccount.id).where(BillingAccount.ledger_seq > 0)))
            .scalars()
            .all()
        )
    written = 0
    for account_id in account_ids:
        try:
            async with session_factory() as session:
                account = await session.get(BillingAccount, account_id)
                if account is None:
                    continue
                have = set(
                    (
                        await session.execute(
                            select(BillingStatement.month).where(
                                BillingStatement.account_id == account_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for month in await lister(session, account, moment):
                    if month >= current or month in have:
                        continue
                    data = await builder(session, account, month, now=moment, live=False)
                    result = await session.execute(
                        insert(BillingStatement)
                        .values(
                            account_id=account_id,
                            tenant_id=account.tenant_id,
                            month=month,
                            currency=data.currency,
                            opening_minor=data.opening_minor,
                            closing_minor=data.closing_minor,
                            data=data.model_dump(mode="json"),
                        )
                        .on_conflict_do_nothing(index_elements=["account_id", "month"])
                    )
                    written += int(result.rowcount or 0)
                await session.commit()
        except Exception as exc:
            logger.error(
                "Statement snapshot for account %s failed (%s)", account_id, type(exc).__name__
            )
    return written


# ------------------------------------------------------------------ formats


def workspace_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "workspace"


def _cell(value: Any) -> Any:
    """A spreadsheet runs a cell that starts with = + - @ as a formula: defuse names. Numbers
    are written as numbers, never through here."""
    from callobuzz_rag.analytics.consumption import _safe_cell

    return _safe_cell(value)


def _major(minor: int) -> str:
    return Money(minor, "INR").amount_str()  # the currency is irrelevant to the digits


def _micro_str(micro: int) -> str:
    whole, frac = divmod(abs(micro), 1_000_000)
    return f"{'-' if micro < 0 else ''}{whole}.{frac:06d}"


def render_csv(data: StatementData, workspace: str) -> str:
    """One flat table: the opening balance, every line, the closing balance, then the usage
    breakdown. Amounts are decimal strings in the statement's currency, never floats."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["section", "date", "description", "answers", "amount", "currency"])
    month_label = data.month.strftime("%Y-%m")
    writer.writerow(["statement", month_label, _cell(workspace), "", "", data.currency])
    writer.writerow(
        [
            "opening",
            data.month.isoformat(),
            "Opening balance",
            "",
            _major(data.opening_minor),
            data.currency,
        ]
    )
    for section in data.sections:
        for line in section.lines:
            writer.writerow(
                [
                    section.key,
                    line.date.isoformat(),
                    _cell(line.description),
                    "",
                    _major(line.amount_minor),
                    data.currency,
                ]
            )
    for row in data.usage_breakdown:
        writer.writerow(
            [
                "usage_detail",
                "",
                _cell(row.label),
                row.answers,
                _micro_str(row.amount_micro),
                data.currency,
            ]
        )
    if data.rounding_carried_micro:
        writer.writerow(
            [
                "usage_detail",
                "",
                "Rounding carried",
                "",
                _micro_str(data.rounding_carried_micro),
                data.currency,
            ]
        )
    for note in data.notes:
        writer.writerow(["note", "", _cell(note), "", "", ""])
    writer.writerow(
        ["closing", "", "Closing balance", "", _major(data.closing_minor), data.currency]
    )
    return out.getvalue()


@cache
def _env() -> Environment:
    return Environment(
        loader=PackageLoader("callobuzz_rag.billing", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html(data: StatementData, workspace: str, branding: Branding) -> str:
    """A standalone printable page (A4 print stylesheet). "Download PDF" prints it. Every value
    reaches the template as text, autoescaped."""
    cur = data.currency

    def money(minor: int) -> str:
        return Money(minor, cur).format()

    sections = [
        {
            "title": s.title,
            "total": money(s.total_minor),
            "lines": [
                {
                    "date": line.date.isoformat(),
                    "description": line.description,
                    "amount": money(line.amount_minor),
                }
                for line in s.lines
            ],
        }
        for s in data.sections
    ]
    breakdown = [
        {
            "label": r.label,
            "answers": r.answers,
            "amount": format_micro(r.amount_micro, cur),
        }
        for r in data.usage_breakdown
    ]
    carried = format_micro(data.rounding_carried_micro, cur) if data.rounding_carried_micro else ""
    return (
        _env()
        .get_template("statement.html")
        .render(
            product=branding.product_name,
            logo_url=branding.logo_url,
            color=branding.primary_color,
            workspace=workspace,
            month_label=data.month.strftime("%B %Y"),
            live=data.live,
            generated=data.generated_at.astimezone(UTC).strftime("%d %b %Y %H:%M UTC"),
            opening=money(data.opening_minor),
            closing=money(data.closing_minor),
            sections=sections,
            breakdown=breakdown,
            carried=carried,
            notes=data.notes,
            powered_text=powered_text_for(branding),
        )
    )
