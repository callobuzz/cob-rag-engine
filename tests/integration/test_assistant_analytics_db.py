"""The outcome SQL on PostgreSQL: previews and test-run spend excluded, resolution (feedback and
handoff rows) and cost as defined, lead figures from the leads table, handoff figures from the
handoffs table."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from tests.integration.conftest import new_tenant_id

pytestmark = pytest.mark.integration


async def test_outcomes_on_postgres(db_session) -> None:  # type: ignore[no-untyped-def]
    from callobuzz_rag.conversation.analytics import build_outcomes, fetch_outcome_rows
    from callobuzz_rag.db.models import Corpus

    tenant_id = await new_tenant_id(db_session)
    corpus = Corpus(
        name=f"c7a-{uuid.uuid4().hex[:6]}",
        tenant_id=tenant_id,
        qdrant_collection=f"corpus_{uuid.uuid4().hex[:12]}",
        api_key_hash="",
    )
    db_session.add(corpus)
    await db_session.flush()
    now = datetime.now(UTC)
    insert = text(
        """INSERT INTO assistant_turns
           (id, corpus_id, tenant_id, conversation_id, channel, label, label_source, language,
            script, route, no_answer, is_preview, rules_fired, offers, claims_removed,
            gate_passed, top_score, timings, question_masked, created_at)
           VALUES (gen_random_uuid(), :c, :t, NULL, 'widget', :label, 'rule', 'hi-Latn', 'latin',
                   :route, :no_answer, :preview, ARRAY['fasting-rule']::varchar[],
                   ARRAY[]::varchar[], 0, :gate, :score, CAST(:timings AS jsonb), :q, :at)"""
    )
    for label, route, no_answer, preview, gate, score in [
        ("business_question", "grounded", False, False, True, 0.52),
        ("business_question", "no_answer", True, False, False, 0.2),
        ("smalltalk", "smalltalk", False, True, None, None),
    ]:
        await db_session.execute(
            insert,
            {
                "c": corpus.id,
                "t": tenant_id,
                "label": label,
                "route": route,
                "no_answer": no_answer,
                "preview": preview,
                "gate": gate,
                "score": score,
                "timings": '{"gate_threshold": 0.5}',
                "q": "Can I drink water?",
                "at": now - timedelta(hours=1),
            },
        )
    await db_session.execute(
        text(
            """INSERT INTO usage_daily
               (id, day, corpus_id, tenant_id, source, requests, prompt_tokens,
                completion_tokens, cost_micro, unpriced_requests)
               VALUES (gen_random_uuid(), :d, :c, :t, 'widget:k', 2, 10, 10, 500000, 0),
                      (gen_random_uuid(), :d, :c, :t, 'eval', 9, 10, 10, 9000000, 0)"""
        ),
        {"d": date.today(), "c": corpus.id, "t": tenant_id},
    )
    rows = await fetch_outcome_rows(
        db_session, [corpus.id], now - timedelta(days=1), now + timedelta(minutes=1)
    )
    assert rows.turns == 2  # the preview turn is excluded
    assert rows.conversations == 2 and rows.resolved == 1
    assert rows.no_answer == 1 and rows.gate_passed == 1 and rows.gated == 2
    assert rows.near_misses == 1  # 0.52 against a 0.5 threshold
    assert rows.rules == [("fasting-rule", 2)]
    assert rows.unanswered == [("can i drink water?", 1)]
    assert rows.cost == 0.5 and rows.cost_known is True  # test-run spend is not a visitor cost
    out = build_outcomes(rows, now, now)
    assert out.cost_per_conversation_usd == 0.25
    assert rows.handoff_reasons == []  # the handoffs table exists and holds no row yet
    assert rows.leads_by_status == []

    # A lead through the leads table.
    await db_session.execute(
        text(
            """INSERT INTO leads (id, corpus_id, tenant_id, channel, fields_encrypted, trigger,
                                  status, page_url, retention_until, created_at)
               VALUES (gen_random_uuid(), :c, :t, 'widget', 'x', 'form', 'won',
                       '/home-collection', now() + interval '30 days', :at)"""
        ),
        {"c": corpus.id, "t": tenant_id, "at": now - timedelta(hours=1)},
    )
    rows = await fetch_outcome_rows(
        db_session, [corpus.id], now - timedelta(days=1), now + timedelta(minutes=1)
    )
    assert rows.leads_by_status == [("won", 1)]
    assert rows.leads_by_page == [("/home-collection", 1)]

    # A rating of 2 or less on the grounded turn makes that conversation unresolved.
    qid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO query_log (id, corpus_id) VALUES (:q, :c)"), {"q": qid, "c": corpus.id}
    )
    await db_session.execute(
        text(
            "UPDATE assistant_turns SET query_id = :q WHERE corpus_id = :c AND route = 'grounded'"
        ),
        {"q": qid, "c": corpus.id},
    )
    await db_session.execute(
        text("INSERT INTO feedback (id, query_log_id, rating) VALUES (gen_random_uuid(), :q, 1)"),
        {"q": qid},
    )
    rows = await fetch_outcome_rows(
        db_session, [corpus.id], now - timedelta(days=1), now + timedelta(minutes=1)
    )
    assert rows.resolved == 0


async def test_a_conversation_with_a_handoff_row_is_not_resolved(db_session) -> None:  # type: ignore[no-untyped-def]
    from callobuzz_rag.conversation.analytics import fetch_outcome_rows
    from callobuzz_rag.db.models import Conversation, Corpus

    tenant_id = await new_tenant_id(db_session)
    corpus = Corpus(
        name=f"c7b-{uuid.uuid4().hex[:6]}",
        tenant_id=tenant_id,
        qdrant_collection=f"corpus_{uuid.uuid4().hex[:12]}",
        api_key_hash="",
    )
    db_session.add(corpus)
    await db_session.flush()
    conv = Conversation(corpus_id=corpus.id)
    db_session.add(conv)
    await db_session.flush()
    now = datetime.now(UTC)
    await db_session.execute(
        text(
            """INSERT INTO assistant_turns
               (id, corpus_id, tenant_id, conversation_id, channel, label, label_source, language,
                script, route, question_masked,
                no_answer, is_preview, rules_fired, offers, claims_removed, created_at)
               VALUES (gen_random_uuid(), :c, :t, :conv, 'widget', 'business_question', 'rule',
                       'en', 'latin', 'grounded', 'hi', false, false, ARRAY[]::varchar[],
                       ARRAY[]::varchar[], 0, :at)"""
        ),
        {"c": corpus.id, "t": tenant_id, "conv": conv.id, "at": now - timedelta(hours=1)},
    )
    window = (now - timedelta(days=1), now + timedelta(minutes=1))
    before = await fetch_outcome_rows(db_session, [corpus.id], *window)
    assert before.conversations == 1 and before.resolved == 1
    await db_session.execute(
        text(
            """INSERT INTO handoffs (id, corpus_id, tenant_id, conversation_id, channel, reason,
                                    created_at)
               VALUES (gen_random_uuid(), :c, :t, :conv, 'callback', 'form', :at)"""
        ),
        {"conv": conv.id, "c": corpus.id, "t": tenant_id, "at": now - timedelta(minutes=30)},
    )
    after = await fetch_outcome_rows(db_session, [corpus.id], *window)
    assert after.conversations == 1 and after.resolved == 0
    assert after.handoff_reasons == [("form", 1)]
