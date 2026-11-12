"""Outcome numbers from query rows (the SQL itself runs against PostgreSQL in integration)."""

from datetime import UTC, datetime

from callobuzz_rag.conversation.analytics import OUTCOME_SQL, OutcomeRows, build_outcomes
from tests.unit.scope_harness import call, scoped_app

START = datetime(2026, 9, 1, tzinfo=UTC)
END = datetime(2026, 10, 1, tzinfo=UTC)


def test_rates_and_cost_per_conversation() -> None:
    rows = OutcomeRows(
        conversations=40,
        resolved=30,
        turns=120,
        no_answer=12,
        off_topic=5,
        abuse=1,
        gated=90,
        gate_passed=81,
        claims_removed=4,
        cost=0.8,
    )
    out = build_outcomes(rows, START, END)
    assert out.resolution_rate == 0.75
    assert out.no_answer_rate == 0.1
    assert out.gate_pass_rate == 0.9
    assert out.cost_per_conversation_usd == 0.02 and out.cost_known is True
    assert out.handoffs is None and out.leads is None and out.handoff_rate is None
    assert len(out.notes) == 2


def test_an_empty_period_has_no_rates() -> None:
    out = build_outcomes(OutcomeRows(), START, END)
    assert out.resolution_rate is None and out.cost_per_conversation_usd is None


def test_a_floor_cost_is_reported_as_unknown() -> None:
    out = build_outcomes(OutcomeRows(conversations=1, cost=0.1, cost_known=False), START, END)
    assert out.cost_known is False


def test_queries_exclude_previews_and_test_run_spend() -> None:
    for name, sql in OUTCOME_SQL.items():
        if "assistant_turns" in sql:
            assert "is_preview = false" in sql, name
    cost = OUTCOME_SQL["cost"]
    assert "sum(cost_micro)" in cost and "unpriced_requests = 0" in cost
    assert "'eval'" not in cost and "source LIKE 'widget:%'" in cost


async def test_the_scope_route_refuses_another_tenants_knowledge_base() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app, "tk_a", "GET", f"/api/v1/assistant/analytics?corpus_id={world.corpus_b.id}"
        )
    assert res.status_code == 403


async def test_the_scope_route_answers_for_the_callers_own_knowledge_base() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app, "tk_a", "GET", f"/api/v1/assistant/analytics?corpus_id={world.corpus_a.id}"
        )
    assert res.status_code == 200, res.text
    assert res.json()["conversations"] == 0


def test_handoff_and_lead_figures_come_from_their_tables_when_present() -> None:
    rows = OutcomeRows(
        conversations=40,
        handoff_reasons=[("explicit_request", 3), ("keyword", 1)],
        handoff_conversations=4,
        leads_by_status=[("new", 6), ("contacted", 3), ("won", 1), ("spam", 5)],
    )
    out = build_outcomes(rows, START, END)
    assert out.handoffs == 4 and out.handoff_rate == 0.1
    assert out.leads == 15 and out.lead_conversion_rate == 0.4  # spam is not in the denominator
    assert out.notes == []


def test_handoffs_count_conversations_not_rows() -> None:
    # three rows (two taps and a call-back) from one conversation, plus an offer elsewhere
    rows = OutcomeRows(
        conversations=2,
        handoff_reasons=[("explicit_request", 3), ("low_confidence", 1)],
        handoff_conversations=2,
    )
    out = build_outcomes(rows, START, END)
    assert out.handoffs == 2 and out.handoff_rate == 1.0
    assert [i.count for i in out.handoff_reasons] == [3, 1]
    sql = OUTCOME_SQL["handoff_conversations"]
    assert "count(DISTINCT COALESCE(conversation_id::text, id::text))" in sql


def test_the_near_miss_count_reads_the_persisted_threshold() -> None:
    assert "gate_threshold" in OUTCOME_SQL["turns"]
    assert build_outcomes(OutcomeRows(near_misses=3), START, END).gate_near_misses == 3


def test_the_handoff_variant_excludes_conversations_with_a_handoff_row() -> None:
    sql = OUTCOME_SQL["conversations_with_handoffs"]
    assert "handoffs" in sql and "conv.conversation_id" in sql
    assert "handoffs" not in OUTCOME_SQL["conversations"]
    assert "AS conversation_id" in OUTCOME_SQL["conversations"]


def test_the_period_starts_at_midnight_utc_so_cost_and_turns_share_days() -> None:
    from callobuzz_rag.api.routes.assistant_analytics import _period

    since, _ = _period(datetime(2026, 9, 25, 10, 30, tzinfo=UTC), None)
    assert since == datetime(2026, 9, 25, tzinfo=UTC)
