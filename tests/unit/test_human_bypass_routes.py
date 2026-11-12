"""TV1: every answering route lets a request for a person through before the question cap,
billing, fair use and the answer lane, free of charge; a normal question in the same state is
unchanged. Spies on the gates; the director itself is covered by test_human_never_refused.py."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.responses import StreamingResponse

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.routes import conversations as conversation_routes
from callobuzz_rag.api.routes import query as query_routes
from callobuzz_rag.api.routes import widget as widget_routes
from callobuzz_rag.api.schemas.query import QueryResponse
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.widget.security import LimitResult
from tests.unit.scope_harness import call, scoped_app
from tests.unit.test_cap_call_sites import quota
from tests.unit.test_widget import SITE, _client, _key, _session, _settings, _use_session

pytestmark = [pytest.mark.assistant_on, pytest.mark.usefixtures("unlimited_plan")]

ASK = "talk to a person"
QUESTION = "what are your timings?"
RUNTIME = SimpleNamespace(config=SimpleNamespace(enabled=True))


def _refused_gate() -> AsyncMock:
    return AsyncMock(side_effect=AssertionError("a gate ran for a request for a person"))


@contextmanager
def gates() -> Iterator[SimpleNamespace]:
    """Every gate of every route, as spies that fail the test if they run."""
    spies = SimpleNamespace(
        query=_refused_gate(),
        conversation=_refused_gate(),
        widget=_refused_gate(),
        slot=_refused_gate(),
        legacy_slot=_refused_gate(),
    )
    with (
        patch.object(query_routes, "answer_gate", spies.query),
        patch.object(conversation_routes, "answer_gate", spies.conversation),
        patch.object(widget_routes, "answer_gate", spies.widget),
        patch.object(query_routes, "hold_slot_or_busy", spies.slot),
        patch.object(widget_routes, "hold_slot_or_busy", spies.legacy_slot),
    ):
        yield spies


def _query_response() -> QueryResponse:
    return QueryResponse(
        answer="card", confidence=1.0, query_id=uuid.uuid4(), model_used="m", latency_ms=1
    )


def _stream() -> AsyncMock:
    return AsyncMock(return_value=StreamingResponse(iter(["event: done\ndata: {}\n\n"])))


# ------------------------------------------------------------------------- /query


async def test_query_lets_a_human_request_through_at_the_cap() -> None:
    answer = AsyncMock(return_value=_query_response())
    with (
        scoped_app() as (app, _),
        quota((2, 2)) as q,
        gates(),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=RUNTIME)),
        patch.object(surfaces, "answer_query", answer),
    ):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": ASK})
    assert res.status_code == 200, res.text
    assert answer.await_args.kwargs["human_only"] is True
    q.assert_not_awaited()  # not counted as a question


async def test_query_a_normal_question_at_the_cap_is_unchanged() -> None:
    answer = AsyncMock()
    with (
        scoped_app() as (app, _),
        quota((2, 2)),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=RUNTIME)),
        patch.object(surfaces, "answer_query", answer),
    ):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": QUESTION})
    assert res.status_code == 429 and res.json()["code"] == "question_cap_reached"
    answer.assert_not_awaited()


async def test_query_with_the_assistant_off_changes_nothing() -> None:
    """No card to give on the legacy path: the gates run as before."""
    with (
        scoped_app() as (app, _),
        quota((2, 2)),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=None)),
    ):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": ASK})
    assert res.status_code == 429 and res.json()["code"] == "question_cap_reached"


# ------------------------------------------------------------------ /query/stream


async def test_stream_lets_a_human_request_through_at_the_cap() -> None:
    stream = _stream()
    with (
        scoped_app() as (app, _),
        quota((2, 2)) as q,
        gates(),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=RUNTIME)),
        patch.object(surfaces, "stream_query_response", stream),
    ):
        res = await call(app, "ck_a", "POST", "/api/v1/query/stream", json={"query": ASK})
    assert res.status_code == 200, res.text
    assert stream.await_args.kwargs["human_only"] is True
    q.assert_not_awaited()


async def test_stream_a_normal_question_at_the_cap_is_unchanged() -> None:
    stream = AsyncMock()
    with (
        scoped_app() as (app, _),
        quota((2, 2)),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=RUNTIME)),
        patch.object(surfaces, "stream_query_response", stream),
    ):
        res = await call(app, "ck_a", "POST", "/api/v1/query/stream", json={"query": QUESTION})
    assert res.status_code == 429
    stream.assert_not_awaited()


# ----------------------------------------------------------------- conversations


async def test_conversation_message_lets_a_human_request_through_at_the_cap() -> None:
    reply = _stream()
    with (
        scoped_app() as (app, world),
        quota((2, 2)) as q,
        gates(),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=RUNTIME)),
        patch.object(surfaces, "conversation_reply", reply),
    ):
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/conversations/{uuid.uuid4()}/messages",
            json={"content": ASK},
        )
    assert res.status_code == 200, res.text
    assert reply.await_args.kwargs["human_only"] is True
    q.assert_not_awaited()


async def test_conversation_message_a_normal_question_at_the_cap_is_unchanged() -> None:
    reply = AsyncMock()
    with (
        scoped_app() as (app, world),
        quota((2, 2)),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=RUNTIME)),
        patch.object(surfaces, "conversation_reply", reply),
    ):
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/conversations/{uuid.uuid4()}/messages",
            json={"content": QUESTION},
        )
    assert res.status_code == 429
    reply.assert_not_awaited()


# ------------------------------------------------------------------------ widget


def _widget_app(chat_bucket: LimitResult, hit: tuple[bool, int] = (True, 0)) -> Any:
    app = create_app()
    app.state.settings = _settings()
    app.state.widget_rate_limiter = SimpleNamespace(
        check=AsyncMock(return_value=chat_bucket), _hit=AsyncMock(return_value=hit)
    )
    return app


async def _widget_chat(app: Any, message: str, reply: AsyncMock) -> Any:
    corpus = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        qdrant_collection="c",
        retrieval_config=None,
        llm_config=None,
        prompt_config=None,
    )
    _use_session(app, _session(_key(corpus.id), corpus))
    with (
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=RUNTIME)),
        patch.object(surfaces, "widget_reply", reply),
    ):
        async with await _client(app) as c:
            return await c.post(
                "/api/v1/widget/chat",
                json={"message": message},
                headers={"X-Widget-Key": "pk_x", "Origin": SITE},
            )


async def test_widget_human_request_passes_the_cap_and_skips_the_chat_bucket() -> None:
    app = _widget_app(LimitResult(False, "rate_limited", 30))  # the chat bucket is used up
    reply = _stream()
    with gates(), quota((2, 2)) as q:
        res = await _widget_chat(app, ASK, reply)
    assert res.status_code == 200, res.text
    assert reply.await_args.kwargs["human_only"] is True
    app.state.widget_rate_limiter.check.assert_not_awaited()
    hit = app.state.widget_rate_limiter._hit
    assert hit.await_args.args[0].startswith("widget:handoff:")  # the handoff bucket instead
    q.assert_not_awaited()


async def test_widget_human_request_with_the_handoff_bucket_used_up_is_429() -> None:
    app = _widget_app(LimitResult(True), hit=(False, 60))
    reply = _stream()
    with gates():
        res = await _widget_chat(app, ASK, reply)
    assert res.status_code == 429 and res.json()["code"] == "handoff_rate_limited"
    reply.assert_not_awaited()


async def test_widget_a_normal_question_with_the_chat_bucket_used_up_is_unchanged() -> None:
    app = _widget_app(LimitResult(False, "rate_limited", 30))
    reply = _stream()
    res = await _widget_chat(app, QUESTION, reply)
    assert res.status_code == 429
    reply.assert_not_awaited()
    app.state.widget_rate_limiter._hit.assert_not_awaited()


async def test_widget_a_normal_question_at_the_cap_is_unchanged() -> None:
    app = _widget_app(LimitResult(True))
    reply = _stream()
    with quota((2, 2)):
        res = await _widget_chat(app, QUESTION, reply)
    assert res.status_code == 429 and res.json()["code"] == "question_cap_reached"
    reply.assert_not_awaited()


# ----------------------------------------------------------------- the turn itself


def test_surfaces_hand_the_flag_to_the_turn() -> None:
    from callobuzz_rag.conversation.state import ConversationState
    from callobuzz_rag.conversation.types import Channel

    turn = surfaces.make_turn(
        corpus=SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4()),
        runtime=MagicMock(),
        text=ASK,
        channel=Channel.API,
        usage_source="api",
        conversation_id=None,
        state=ConversationState(),
        history=[],
        page_url=None,
        session_token=None,
        opts=None,
        one_off=None,
        human_only=True,
    )
    assert turn.human_only is True and turn.model_free is True


def test_is_human_turn_needs_the_assistant_on() -> None:
    assert surfaces.is_human_turn(RUNTIME, ASK) is True
    assert surfaces.is_human_turn(RUNTIME, QUESTION) is False
    assert surfaces.is_human_turn(None, ASK) is False
    off = SimpleNamespace(config=SimpleNamespace(enabled=False))
    assert surfaces.is_human_turn(off, ASK) is False
