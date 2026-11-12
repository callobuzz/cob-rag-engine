"""Search mode: matching help pages, no model call, when the plan's questions are used up or no
language-model key is set."""

import json

from callobuzz_rag.db.models.message import Message
from tests.unit.cob_harness import tokens


async def test_at_the_cap_no_model_and_search_results(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (100, 100)
    events = await cob.sse("alice", {"message": "How do I rotate a key?"})
    assert cob.director.built == 0  # the director, and so every model call, never ran
    meta = events[0].data
    ui = [e.data for e in events if e.event == "ui"]
    assert meta["mode"] == "search" and ui[0]["kind"] == "cob_search_results"
    assert ui[0]["reason"] == "question_cap_reached" and 1 <= len(ui[0]["items"]) <= 5
    assert events[-1].event == "done" and events[-1].data["counted"] is False
    assert events[-1].data["mode"] == "search"
    text = tokens(events)
    assert "used this month's questions" in text and "Answers come back on 1 " in text


async def test_the_search_runs_without_query_understanding_and_with_the_role_filter(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (5, 9)
    await cob.sse("ravi", {"message": "How do I rotate a key?"})
    call = cob.retriever.calls[-1]
    assert call["enable_classification"] is False and call["enable_hyde"] is False
    assert call["enable_reranking"] is False
    assert call["metadata_filters"] == {"audience": ["customer", "public"]}


async def test_search_results_never_include_administrator_pages(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (1, 1)
    events = await cob.sse("alice", {"message": "engine settings"})
    items = next(e.data for e in events if e.event == "ui")["items"]
    slugs = [i["slug"] for i in items]
    assert slugs and "screens/engine" not in slugs
    assert len(set(slugs)) == len(slugs)
    assert set(items[0]) == {"index", "slug", "anchor", "title", "snippet", "route", "public_url"}
    assert "chunk_id" not in json.dumps(items)


async def test_an_administrator_is_never_capped(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (1, 99)
    events = await cob.sse("sa", {"message": "hi"})
    assert events[0].data["mode"] == "answer"
    assert cob.director.built == 1


async def test_the_operator_workspace_is_unlimited_through_its_plan(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = None  # the Internal plan has no monthly limit
    events = await cob.sse("super_op", {"message": "hi"})
    assert events[0].data["mode"] == "answer"


async def test_below_the_cap_answers_normally(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (100, 99)
    events = await cob.sse("alice", {"message": "hi"})
    assert events[0].data["mode"] == "answer"


async def test_no_language_model_key_is_search_mode_with_its_own_reason(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.llm_ok = False
    events = await cob.sse("alice", {"message": "How do I rotate a key?"})
    ui = next(e.data for e in events if e.event == "ui")
    assert ui["reason"] == "llm_unavailable" and events[0].data["mode"] == "search"
    assert "can't write answers right now" in tokens(events)
    assert cob.director.built == 0


async def test_the_cap_reason_wins_over_a_missing_key(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (1, 1)
    cob.world.llm_ok = False
    events = await cob.sse("alice", {"message": "x"})
    assert next(e.data for e in events if e.event == "ui")["reason"] == "question_cap_reached"


async def test_a_search_with_no_match_says_so(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (1, 1)
    cob.retriever.records = [("screens/engine", 0.9)]  # only administrator pages: none for an owner
    events = await cob.sse("alice", {"message": "zzz"})
    ui = next(e.data for e in events if e.event == "ui")
    assert ui["items"] == [] and "found no help page" in tokens(events)


async def test_the_search_turn_is_stored_with_its_results(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (1, 1)
    events = await cob.sse("alice", {"message": "How do I rotate a key?"})
    conv = events[0].data["conversation_id"]
    stored = (await cob.get("alice", f"/api/v1/cob/conversations/{conv}")).json()["messages"]
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert stored[1]["ui"][0]["kind"] == "cob_search_results"
    assert len(cob.db.all(Message)) == 2


async def test_a_search_failure_is_an_error_event(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (1, 1)

    async def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("qdrant down")

    cob.retriever.run_retrieval_pipeline = boom  # type: ignore[method-assign]
    events = await cob.sse("alice", {"message": "x"})
    assert events[-1].event == "error" and events[-1].data["code"] == "retrieval_failed"


async def test_hindi_message_gets_a_hindi_sentence(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (1, 1)
    events = await cob.sse("alice", {"message": "मैं कुंजी कैसे बदलूँ?"})
    assert events[0].data["language"] == "hi" and "सवाल पूरे हो गए" in tokens(events)


async def test_a_paused_balance_is_search_mode_with_its_own_reason(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.paused = True
    events = await cob.sse("alice", {"message": "How do I rotate a key?"})
    assert cob.director.built == 0  # no model call while the balance is empty
    assert events[0].data["mode"] == "search"
    ui = next(e.data for e in events if e.event == "ui")
    assert ui["kind"] == "cob_search_results" and ui["reason"] == "balance_paused"
    assert events[-1].data["counted"] is False


async def test_the_cap_reason_wins_over_a_paused_balance(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (1, 1)
    cob.world.paused = True
    events = await cob.sse("alice", {"message": "x"})
    assert next(e.data for e in events if e.event == "ui")["reason"] == "question_cap_reached"


async def test_an_administrator_is_never_paused(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.paused = True
    events = await cob.sse("sa", {"message": "hi"})
    assert events[0].data["mode"] == "answer"


async def test_a_failing_billing_read_still_answers(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.billing_error = True
    events = await cob.sse("alice", {"message": "How do I rotate a key?"})
    assert events[0].data["mode"] == "answer"
    assert cob.director.built == 1


# PIP3 fair use: only the business's daily COB questions limit COB (it keeps its own model)
async def test_a_business_over_its_daily_cob_questions_is_search_mode_with_its_own_reason(
    cob,
) -> None:  # type: ignore[no-untyped-def]
    from unittest.mock import AsyncMock, patch

    with patch("callobuzz_rag.fairuse.gate.cob_blocked", AsyncMock(return_value=True)) as blocked:
        events = await cob.sse("alice", {"message": "How do I rotate a key?"})
    assert cob.director.built == 0  # no model call
    assert events[0].data["mode"] == "search"
    ui = next(e.data for e in events if e.event == "ui")
    assert ui["kind"] == "cob_search_results" and ui["reason"] == "fair_use"
    assert events[-1].data["counted"] is False
    assert blocked.await_args.kwargs["enabled"] is True


async def test_the_cap_and_the_balance_win_over_the_daily_cob_questions(cob) -> None:  # type: ignore[no-untyped-def]
    from unittest.mock import AsyncMock, patch

    with patch("callobuzz_rag.fairuse.gate.cob_blocked", AsyncMock(return_value=True)) as blocked:
        cob.world.quota = (1, 1)
        capped = await cob.sse("alice", {"message": "x"})
        cob.world.quota = None
        cob.world.paused = True
        paused = await cob.sse("alice", {"message": "x"})
    assert next(e.data for e in capped if e.event == "ui")["reason"] == "question_cap_reached"
    assert next(e.data for e in paused if e.event == "ui")["reason"] == "balance_paused"
    blocked.assert_not_awaited()


async def test_an_administrator_and_a_business_under_its_limit_answer_normally(cob) -> None:  # type: ignore[no-untyped-def]
    from unittest.mock import AsyncMock, patch

    with patch("callobuzz_rag.fairuse.gate.cob_blocked", AsyncMock(return_value=False)):
        under = await cob.sse("alice", {"message": "hi"})
    with patch("callobuzz_rag.fairuse.gate.cob_blocked", AsyncMock(return_value=True)) as blocked:
        admin = await cob.sse("sa", {"message": "hi"})
    assert under[0].data["mode"] == "answer" and admin[0].data["mode"] == "answer"
    blocked.assert_not_awaited()  # the platform administrator is never limited
