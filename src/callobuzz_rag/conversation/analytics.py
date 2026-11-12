"""Outcome analytics over assistant turns, feedback and usage (conversation C7).

Handoffs and leads belong to C4 and C5. Their tables are looked up at request time: while a
table does not exist the matching figures are null and `notes` says so.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.schemas.assistant_quality import CountItem, OutcomesResponse

# Visitor traffic only: preview turns (the playground) are left out.
_TURNS = """
    SELECT * FROM assistant_turns
    WHERE corpus_id = ANY(:ids) AND is_preview = false
      AND created_at >= :start AND created_at < :end
"""

_NO_HANDOFF_ROW = """
                   AND NOT EXISTS (SELECT 1 FROM handoffs h
                                   WHERE h.conversation_id = conv.conversation_id)"""

OUTCOME_SQL: dict[str, str] = {
    "conversations": f"""
        WITH t AS ({_TURNS}),
        conv AS (
          SELECT COALESCE(conversation_id::text, id::text) AS cid,
                 (array_agg(conversation_id))[1] AS conversation_id,
                 bool_or(route IN ('grounded', 'verbatim')) AS answered,
                 bool_or(route = 'handoff' OR 'handoff_card' = ANY(offers)) AS offered_handoff,
                 (array_agg(route ORDER BY created_at DESC))[1] AS last_route,
                 array_remove(array_agg(query_id), NULL) AS qids
          FROM t GROUP BY 1
        )
        SELECT count(*) AS conversations,
               count(*) FILTER (
                 WHERE answered AND NOT offered_handoff AND last_route <> 'no_answer'
                   AND NOT EXISTS (SELECT 1 FROM feedback f
                                   WHERE f.query_log_id = ANY(conv.qids) AND f.rating <= 2)
               ) AS resolved
        FROM conv
    """,
    "turns": f"""
        WITH t AS ({_TURNS})
        SELECT count(*) AS turns,
               count(*) FILTER (WHERE no_answer) AS no_answer,
               count(*) FILTER (WHERE label = 'off_topic') AS off_topic,
               count(*) FILTER (WHERE label = 'abuse') AS abuse,
               count(*) FILTER (WHERE gate_passed IS NOT NULL) AS gated,
               count(*) FILTER (WHERE gate_passed) AS gate_passed,
               count(*) FILTER (
                 WHERE gate_passed AND top_score IS NOT NULL
                   AND (timings->>'gate_threshold') IS NOT NULL
                   AND top_score - (timings->>'gate_threshold')::float < 0.05
               ) AS near_misses,
               COALESCE(sum(claims_removed), 0) AS claims_removed
        FROM t
    """,
    "labels": f"WITH t AS ({_TURNS}) SELECT label AS key, count(*) AS count FROM t "
    "GROUP BY 1 ORDER BY 2 DESC",
    "languages": f"WITH t AS ({_TURNS}) SELECT language AS key, count(*) AS count FROM t "
    "WHERE language IS NOT NULL GROUP BY 1 ORDER BY 2 DESC",
    "rules": f"WITH t AS ({_TURNS}) SELECT r AS key, count(*) AS count "
    "FROM t, unnest(rules_fired) AS r GROUP BY 1 ORDER BY 2 DESC LIMIT 50",
    "unanswered": f"""
        WITH t AS ({_TURNS})
        SELECT lower(btrim(question_masked)) AS key, count(*) AS count
        FROM t WHERE no_answer AND question_masked IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """,
    # Only run when the table exists (`to_regclass`): C5 / C4 land them after this module.
    "handoff_reasons": """
        SELECT COALESCE(reason, 'unknown') AS key, count(*) AS count FROM handoffs
        WHERE corpus_id = ANY(:ids) AND created_at >= :start AND created_at < :end
        GROUP BY 1 ORDER BY 2 DESC
    """,
    # A conversation can hold several rows (a tap on each channel, a call-back): count it once.
    "handoff_conversations": """
        SELECT count(DISTINCT COALESCE(conversation_id::text, id::text)) AS n FROM handoffs
        WHERE corpus_id = ANY(:ids) AND created_at >= :start AND created_at < :end
    """,
    "leads_by_status": """
        SELECT status AS key, count(*) AS count FROM leads
        WHERE corpus_id = ANY(:ids) AND created_at >= :start AND created_at < :end
        GROUP BY 1 ORDER BY 2 DESC
    """,
    "leads_by_page": """
        SELECT COALESCE(page_url, '(no page)') AS key, count(*) AS count FROM leads
        WHERE corpus_id = ANY(:ids) AND created_at >= :start AND created_at < :end
        GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """,
    # Test-run spend (source 'eval') is not a visitor conversation cost. `cost_micro` holds only
    # the priced part: any unpriced request makes the total a floor (`cost_known` false).
    "cost": """
        SELECT COALESCE(sum(cost_micro), 0) AS cost_micro,
               COALESCE(bool_and(unpriced_requests = 0), true) AS cost_known
        FROM usage_daily
        WHERE corpus_id = ANY(:ids) AND day >= CAST(:start AS date) AND day <= CAST(:end AS date)
          AND (source IN ('api', 'stream', 'conversation') OR source LIKE 'widget:%')
    """,
}

# Once C5's `handoffs` table exists, a conversation with a handoff row is not "resolved without
# a person" even when no turn offered one. Same query, one more condition.
_RESOLVED_END = "f.rating <= 2)\n               ) AS resolved"
assert _RESOLVED_END in OUTCOME_SQL["conversations"]
OUTCOME_SQL["conversations_with_handoffs"] = OUTCOME_SQL["conversations"].replace(
    _RESOLVED_END, "f.rating <= 2)" + _NO_HANDOFF_ROW + "\n               ) AS resolved"
)


@dataclass
class OutcomeRows:
    conversations: int = 0
    resolved: int = 0
    turns: int = 0
    no_answer: int = 0
    off_topic: int = 0
    abuse: int = 0
    gated: int = 0
    gate_passed: int = 0
    near_misses: int = 0
    claims_removed: int = 0
    labels: list[tuple[str, int]] = field(default_factory=list)
    languages: list[tuple[str, int]] = field(default_factory=list)
    rules: list[tuple[str, int]] = field(default_factory=list)
    unanswered: list[tuple[str, int]] = field(default_factory=list)
    handoff_reasons: list[tuple[str, int]] | None = None  # None: no handoffs table yet
    handoff_conversations: int = 0
    leads_by_status: list[tuple[str, int]] | None = None  # None: no leads table yet
    leads_by_page: list[tuple[str, int]] = field(default_factory=list)
    cost: float = 0.0
    cost_known: bool = True


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _items(pairs: list[tuple[str, int]]) -> list[CountItem]:
    return [CountItem(key=str(k), count=int(c)) for k, c in pairs]


# Lead statuses that count as followed up (C4's LEAD_STATUSES).
CONVERTED_STATUSES = ("contacted", "won")


def build_outcomes(rows: OutcomeRows, start: datetime, end: datetime) -> OutcomesResponse:
    notes: list[str] = []
    handoffs = leads = None
    lead_rate = handoff_rate = None
    if rows.handoff_reasons is None:
        notes.append("Handoff figures appear once talk-to-a-person is available.")
    else:
        handoffs = rows.handoff_conversations
        handoff_rate = _rate(handoffs, rows.conversations)
    if rows.leads_by_status is None:
        notes.append("Lead figures appear once lead capture is available.")
    else:
        leads = sum(c for _, c in rows.leads_by_status)
        converted = sum(c for k, c in rows.leads_by_status if k in CONVERTED_STATUSES)
        real = sum(c for k, c in rows.leads_by_status if k != "spam")  # spam is not a lead
        lead_rate = _rate(converted, real)
    return OutcomesResponse(
        start=start,
        end=end,
        conversations=rows.conversations,
        turns=rows.turns,
        resolved=rows.resolved,
        resolution_rate=_rate(rows.resolved, rows.conversations),
        no_answer_turns=rows.no_answer,
        no_answer_rate=_rate(rows.no_answer, rows.turns),
        top_unanswered=_items(rows.unanswered),
        languages=_items(rows.languages),
        labels=_items(rows.labels),
        off_topic=rows.off_topic,
        abuse=rows.abuse,
        gate_pass_rate=_rate(rows.gate_passed, rows.gated),
        gate_near_misses=rows.near_misses,
        handoffs=handoffs,
        handoff_rate=handoff_rate,
        handoff_reasons=_items(rows.handoff_reasons or []),
        leads=leads,
        leads_by_status=_items(rows.leads_by_status or []),
        leads_by_page=_items(rows.leads_by_page),
        lead_conversion_rate=lead_rate,
        notes=notes,
        claims_removed=rows.claims_removed,
        rules_fired=_items(rows.rules),
        cost_usd=round(rows.cost, 6),
        cost_known=rows.cost_known,
        cost_per_conversation_usd=(
            round(rows.cost / rows.conversations, 6) if rows.conversations else None
        ),
    )


async def fetch_outcome_rows(
    session: AsyncSession, corpus_ids: list[uuid.UUID], start: datetime, end: datetime
) -> OutcomeRows:
    params: dict[str, Any] = {"ids": corpus_ids, "start": start, "end": end}
    ids_param = bindparam("ids", type_=ARRAY(UUID(as_uuid=True)))

    async def run(name: str) -> Any:
        return await session.execute(text(OUTCOME_SQL[name]).bindparams(ids_param), params)

    rows = OutcomeRows()

    async def exists(name: str) -> bool:
        res = await session.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name})
        return bool(res.scalar_one())

    has_handoffs = await exists("handoffs")
    conv = (await run("conversations_with_handoffs" if has_handoffs else "conversations")).one()
    rows.conversations, rows.resolved = int(conv.conversations), int(conv.resolved)
    t = (await run("turns")).one()
    rows.turns, rows.no_answer = int(t.turns), int(t.no_answer)
    rows.off_topic, rows.abuse = int(t.off_topic), int(t.abuse)
    rows.gated, rows.gate_passed = int(t.gated), int(t.gate_passed)
    rows.near_misses = int(t.near_misses)
    rows.claims_removed = int(t.claims_removed)
    for name in ("labels", "languages", "rules", "unanswered"):
        setattr(rows, name, [(r.key, int(r.count)) for r in (await run(name)).all()])

    if has_handoffs:
        rows.handoff_reasons = [(r.key, int(r.count)) for r in (await run("handoff_reasons")).all()]
        rows.handoff_conversations = int((await run("handoff_conversations")).one().n)
    if await exists("leads"):
        rows.leads_by_status = [(r.key, int(r.count)) for r in (await run("leads_by_status")).all()]
        rows.leads_by_page = [(r.key, int(r.count)) for r in (await run("leads_by_page")).all()]
    cost = (await run("cost")).one()
    rows.cost = int(cost.cost_micro) / 1_000_000
    rows.cost_known = bool(cost.cost_known)
    return rows
