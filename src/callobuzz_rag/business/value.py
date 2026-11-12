"""The value report: what the assistant did for a business in a month, and an estimate in money
(TV4).

`estimate` is pure and integer-only (money is minor units, never a float): leads that count
(qualified by the owner's rules, or every lead when there are no rules) x the owner's value of a lead, requests to talk to a person x their value, and the minutes the
assistant saved x the owner's staff cost. The result always carries its formula lines, so every
screen can show "based on your settings"; it is an estimate, never a promise.

`fetch_inputs` reads the figures for one workspace and one time window (all of its knowledge
bases; only visitor channels, no previews, no spam). Definitions are pinned in the trust epics' README.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_TIMEZONE = "Asia/Kolkata"
QUESTION_LABELS = ("business_question", "buying_intent", "other", "domain_explainer")
ANSWERED_ROUTES = ("grounded", "verbatim")
TOP_N = 10

# Only what visitors wrote counts. The dashboard Playground and the onboarding Try step answer on
# `stream`, a business testing its knowledge base by key answers on `api`, and COB answers on
# `cob`: none of those is a customer. A new channel must be listed in one of the two sets
# (a unit test pins that they cover `Channel`).
VISITOR_CHANNELS = frozenset({"widget", "whatsapp", "conversation"})
NON_VISITOR_CHANNELS = frozenset({"api", "stream", "playground", "cob"})
_VISITOR_SQL = ", ".join(f"'{c}'" for c in sorted(VISITOR_CHANNELS))


@dataclass(frozen=True)
class ValueInputs:
    """One workspace, one month (or month to date)."""

    conversations: int
    questions: int
    answered: int
    answered_after_hours: int
    leads_total: int
    leads_qualified: int  # the leads that count: qualified, or not rated (no rules defined)
    leads_partial: int
    handoffs: int
    bookings: int
    median_first_reply_ms: int | None
    top_questions: list[tuple[str, int]]
    unanswered: list[tuple[str, int]]
    leads_unrated: int = 0  # of `leads_qualified`: leads of a knowledge base with no rules


@dataclass(frozen=True)
class ValueSettingsIn:
    """Minor units of the workspace currency (paise or cents)."""

    lead_value_minor: int
    handoff_value_minor: int
    staff_hour_minor: int
    minutes_per_answer: int


@dataclass(frozen=True)
class FormulaLine:
    """One term of the estimate. `inputs` are the numbers it was computed from, so a screen
    can print "9 qualified leads x Rs 1,500" without doing any money arithmetic."""

    key: str
    inputs: dict[str, int]
    amount_minor: int


@dataclass(frozen=True)
class ValueBreakdown:
    leads_minor: int
    handoffs_minor: int
    minutes_saved: int
    time_minor: int
    total_minor: int
    formula: list[FormulaLine] = field(default_factory=list)


def estimate(i: ValueInputs, s: ValueSettingsIn) -> ValueBreakdown:
    """The estimated value of `i` under the owner's settings `s`. Integers only; the time
    term floors to a whole minor unit."""
    leads_minor = i.leads_qualified * s.lead_value_minor
    handoffs_minor = i.handoffs * s.handoff_value_minor
    minutes_saved = i.answered * s.minutes_per_answer
    time_minor = minutes_saved * s.staff_hour_minor // 60
    return ValueBreakdown(
        leads_minor=leads_minor,
        handoffs_minor=handoffs_minor,
        minutes_saved=minutes_saved,
        time_minor=time_minor,
        total_minor=leads_minor + handoffs_minor + time_minor,
        formula=[
            FormulaLine(
                "leads",
                {"count": i.leads_qualified, "value_minor": s.lead_value_minor},
                leads_minor,
            ),
            FormulaLine(
                "handoffs",
                {"count": i.handoffs, "value_minor": s.handoff_value_minor},
                handoffs_minor,
            ),
            FormulaLine(
                "time",
                {"minutes_saved": minutes_saved, "staff_hour_minor": s.staff_hour_minor},
                time_minor,
            ),
        ],
    )


# ------------------------------------------------------------------ months


def valid_timezone(name: str | None) -> str:
    try:
        ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_TIMEZONE
    return name or DEFAULT_TIMEZONE


def month_bounds(month: date, tz: str) -> tuple[datetime, datetime]:
    """[start, end) of the calendar month containing `month`, in the business's time zone,
    returned in UTC."""
    zone = ZoneInfo(valid_timezone(tz))
    first = month.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    return (
        datetime.combine(first, time.min, tzinfo=zone).astimezone(UTC),
        datetime.combine(following, time.min, tzinfo=zone).astimezone(UTC),
    )


def month_of(now: datetime, tz: str) -> date:
    """The first day of the business's current month."""
    return now.astimezone(ZoneInfo(valid_timezone(tz))).date().replace(day=1)


def previous_month(first: date) -> date:
    return (first - timedelta(days=1)).replace(day=1)


# ------------------------------------------------------------------ the numbers

_TURNS = f"""
    SELECT * FROM assistant_turns
    WHERE tenant_id = :tid AND is_preview = false AND channel IN ({_VISITOR_SQL})
      AND corpus_id IN (SELECT id FROM corpora WHERE tenant_id = :tid AND NOT is_system)
      AND created_at >= :start AND created_at < :end
"""

_COUNTS_SQL = f"""
    WITH t AS ({_TURNS})
    SELECT
      (SELECT count(DISTINCT conversation_id) FROM t) AS conversations,
      (SELECT count(*) FROM t WHERE label = ANY(:question_labels)) AS questions,
      (SELECT count(*) FROM t
         WHERE label = ANY(:question_labels) AND route = ANY(:answered_routes)) AS answered,
      (SELECT count(*) FROM t
         WHERE label = ANY(:question_labels) AND route = ANY(:answered_routes)
           AND office_open IS FALSE) AS answered_after_hours
"""

# `qualification` is NULL only when the knowledge base had no rules when the lead came in: such a
# lead is not rated, so it counts as a lead (a workspace that never wrote rules would otherwise
# value none of its leads).
_LEADS_SQL = """
    SELECT count(*) AS total,
           count(*) FILTER (WHERE qualification = 'qualified') AS qualified,
           count(*) FILTER (WHERE qualification = 'partial') AS partial,
           count(*) FILTER (WHERE jsonb_typeof(callback_window) = 'object') AS callbacks,
           count(*) FILTER (WHERE qualification IS NULL) AS unrated
    FROM leads
    WHERE tenant_id = :tid AND status <> 'spam'
      AND created_at >= :start AND created_at < :end
"""

# A "call me back" form makes a lead and a handoff of the same visitor action. When the lead
# already counts (qualified, or not rated) it carries the value, so that handoff is not a second
# request; a callback whose lead does not count (partial, unqualified, spam) is still counted here.
_HANDOFFS_SQL = """
    SELECT count(DISTINCT COALESCE(h.conversation_id::text, h.id::text)) FILTER (
             WHERE NOT (l.id IS NOT NULL AND l.status <> 'spam'
                        AND (l.qualification IS NULL OR l.qualification = 'qualified'))
           ) AS total,
           count(DISTINCT COALESCE(h.conversation_id::text, h.id::text))
             FILTER (WHERE h.channel = 'booking') AS bookings
    FROM handoffs h LEFT JOIN leads l ON l.id = h.lead_id
    WHERE h.tenant_id = :tid AND h.created_at >= :start AND h.created_at < :end
"""

_FIRST_REPLY_SQL = f"""
    WITH t AS ({_TURNS}),
    first_turn AS (
      SELECT DISTINCT ON (conversation_id) conversation_id, latency_ms
      FROM t WHERE conversation_id IS NOT NULL
      ORDER BY conversation_id, created_at, id
    )
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS median_ms FROM first_turn
"""

_NORMALISED = "regexp_replace(lower(btrim(question_masked)), '\\s+', ' ', 'g')"

_TOP_SQL = f"""
    WITH t AS ({_TURNS})
    SELECT {_NORMALISED} AS key, count(*) AS count
    FROM t WHERE label = ANY(:question_labels) AND question_masked IS NOT NULL
      AND btrim(question_masked) <> ''
    GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {TOP_N}
"""

_UNANSWERED_SQL = f"""
    WITH t AS ({_TURNS})
    SELECT {_NORMALISED} AS key, count(*) AS count
    FROM t WHERE no_answer AND question_masked IS NOT NULL AND btrim(question_masked) <> ''
    GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {TOP_N}
"""


async def fetch_inputs(
    session: "AsyncSession", tenant_id: uuid.UUID, start: datetime, end: datetime
) -> ValueInputs:
    """The figures of one workspace between `start` (included) and `end` (excluded)."""
    window: dict[str, Any] = {"tid": tenant_id, "start": start, "end": end}
    counts = (
        await session.execute(
            text(_COUNTS_SQL),
            {
                **window,
                "question_labels": list(QUESTION_LABELS),
                "answered_routes": list(ANSWERED_ROUTES),
            },
        )
    ).one()
    leads = (await session.execute(text(_LEADS_SQL), window)).one()
    handoffs = (await session.execute(text(_HANDOFFS_SQL), window)).one()
    median = (await session.execute(text(_FIRST_REPLY_SQL), window)).scalar()
    top = (
        await session.execute(text(_TOP_SQL), {**window, "question_labels": list(QUESTION_LABELS)})
    ).all()
    unanswered = (await session.execute(text(_UNANSWERED_SQL), window)).all()
    return ValueInputs(
        conversations=int(counts[0] or 0),
        questions=int(counts[1] or 0),
        answered=int(counts[2] or 0),
        answered_after_hours=int(counts[3] or 0),
        leads_total=int(leads[0] or 0),
        leads_qualified=int(leads[1] or 0) + int(leads[4] or 0),
        leads_unrated=int(leads[4] or 0),
        leads_partial=int(leads[2] or 0),
        handoffs=int(handoffs[0] or 0),
        bookings=int(handoffs[1] or 0) + int(leads[3] or 0),
        median_first_reply_ms=int(median) if median is not None else None,
        top_questions=[(str(r[0]), int(r[1])) for r in top],
        unanswered=[(str(r[0]), int(r[1])) for r in unanswered],
    )
