"""The billing hook at every answering route (BIL4 task 3): a paused workspace gets the pause
reply through the director, a 402 on the legacy paths, a 409 `assistant_disabled` on the widget,
and nothing at all changes for an unbilled tenant or the admin principal."""

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.responses import Response

from callobuzz_rag.billing.enforcement import NOT_BILLED, BillingState
from tests.unit.scope_harness import call, scoped_app
from tests.unit.test_cap_call_sites import assistant_on, cached_answer, quota

PAUSED = BillingState(True, True, "prepaid_empty", -120, "INR")
FORBIDDEN_WORDS = ("balance", "billing", "payment", "plan", "402", "wallet", "₹", "credit")


@contextmanager
def state(value: BillingState) -> Any:
    mock = AsyncMock(return_value=value)
    with patch("callobuzz_rag.billing.enforcement.billing_state", mock):
        yield mock


def _assert_402(res: Any) -> None:
    assert res.status_code == 402, res.text
    body = res.json()
    assert body["code"] == "billing_paused" and body["reason"] == "prepaid_empty"
    assert "available" not in body and "amount" not in body
    assert not any(ch.isdigit() for ch in body["detail"])  # no amount in the sentence


# --------------------------------------------------------------- the assistant is off


async def test_query_is_402_when_paused() -> None:
    with scoped_app() as (app, _), quota(None), state(PAUSED):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    _assert_402(res)


async def test_a_paused_workspace_never_gets_a_cached_answer() -> None:
    with scoped_app() as (app, _), quota(None), cached_answer() as cache, state(PAUSED):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    _assert_402(res)
    assert cache.get.await_count <= 1  # looked up at most; never served


async def test_stream_is_402_when_paused() -> None:
    with scoped_app() as (app, _), quota(None), state(PAUSED):
        res = await call(app, "ck_a", "POST", "/api/v1/query/stream", json={"query": "hi"})
    _assert_402(res)


async def test_conversation_message_is_402_when_paused() -> None:
    with scoped_app() as (app, world), quota(None), state(PAUSED):
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/conversations/{uuid.uuid4()}/messages",
            json={"content": "hi"},
        )
    _assert_402(res)


async def test_retrieve_is_never_checked() -> None:
    with scoped_app() as (app, _), state(PAUSED) as s:
        res = await call(app, "ck_a", "POST", "/api/v1/query/retrieve", json={"query": "hi"})
    assert res.status_code != 402
    s.assert_not_awaited()


@pytest.mark.parametrize("key", ["admin", "sa"])
async def test_the_admin_principal_passes(key: str) -> None:
    with scoped_app() as (app, world), quota(None), cached_answer(), state(PAUSED) as s:
        res = await call(
            app,
            key,
            "POST",
            "/api/v1/query",
            json={"query": "hi", "corpus_id": str(world.corpus_a.id)},
        )
    assert res.status_code == 200, res.text
    s.assert_not_awaited()


async def test_an_unbilled_tenant_is_unchanged() -> None:
    with scoped_app() as (app, _), quota(None), cached_answer(), state(NOT_BILLED):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 200, res.text
    assert res.json()["answer"] == "From the cache"


async def test_golden_run_start_is_402_when_paused() -> None:
    from callobuzz_rag.api.routes import evals

    request = SimpleNamespace(
        state=SimpleNamespace(is_admin=False),
        app=SimpleNamespace(state=SimpleNamespace(session_factory=None, settings=None)),
    )
    corpus = SimpleNamespace(tenant_id=uuid.uuid4(), llm_config=None)
    with quota(None), state(PAUSED), pytest.raises(Exception) as err:
        await evals._check_question_room(AsyncMock(), request, corpus, 3)  # type: ignore[arg-type]
    assert getattr(err.value, "status_code", None) == 402
    assert getattr(err.value, "code", None) == "billing_paused"


async def test_golden_run_start_judges_a_byok_knowledge_base_as_byok() -> None:
    from callobuzz_rag.api.routes import evals

    request = SimpleNamespace(
        state=SimpleNamespace(is_admin=False),
        app=SimpleNamespace(state=SimpleNamespace(session_factory=None, settings=None)),
    )
    corpus = SimpleNamespace(tenant_id=uuid.uuid4(), llm_config={"api_key_encrypted": "x"})
    with (
        quota(None),
        state(NOT_BILLED) as billing,  # a byok_mode=free plan never bills the knowledge base's key
        patch(
            "callobuzz_rag.accounts.caps.overage_lets_through", AsyncMock(return_value=True)
        ) as overage,
    ):
        await evals._check_question_room(AsyncMock(), request, corpus, 3)  # type: ignore[arg-type]
    assert billing.await_args.kwargs["payer"] == "byok"
    assert overage.await_args.args[2] == "byok"


# ---------------------------------------------------------------- the assistant is on


@pytest.mark.assistant_on
async def test_query_with_the_assistant_on_runs_the_director_paused() -> None:
    from callobuzz_rag.api.schemas.query import QueryResponse

    done = QueryResponse(
        answer="pause message",
        confidence=1.0,
        query_id=uuid.uuid4(),
        model_used="m",
        latency_ms=1,
        paused=True,
    )
    with scoped_app() as (app, _), quota(None), state(PAUSED), assistant_on() as (answer, _):
        answer.return_value = done
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 200, res.text
    assert answer.await_args.kwargs["billing_paused"] is True
    assert res.json()["paused"] is True


@pytest.mark.assistant_on
async def test_stream_with_the_assistant_on_runs_the_director_paused() -> None:
    with scoped_app() as (app, _), quota(None), state(PAUSED), assistant_on() as (_, stream):
        stream.return_value = MagicMock()
        await call(app, "ck_a", "POST", "/api/v1/query/stream", json={"query": "hi"})
    assert stream.await_args.kwargs["billing_paused"] is True


@pytest.mark.assistant_on
async def test_an_unpaused_workspace_runs_the_director_normally() -> None:
    from callobuzz_rag.api.schemas.query import QueryResponse

    done = QueryResponse(
        answer="ok", confidence=1.0, query_id=uuid.uuid4(), model_used="m", latency_ms=1
    )
    with scoped_app() as (app, _), quota(None), state(NOT_BILLED), assistant_on() as (answer, _):
        answer.return_value = done
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 200 and res.json()["paused"] is False
    assert answer.await_args.kwargs["billing_paused"] is False


@pytest.mark.assistant_on
async def test_conversation_message_with_the_assistant_on_runs_the_director_paused() -> None:
    from callobuzz_rag.conversation import surfaces

    runtime = SimpleNamespace(config=SimpleNamespace(enabled=True))
    reply = AsyncMock(return_value=Response(status_code=201))
    with (
        scoped_app() as (app, world),
        quota(None),
        state(PAUSED),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch.object(surfaces, "conversation_reply", reply),
    ):
        await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/conversations/{uuid.uuid4()}/messages",
            json={"content": "hi"},
        )
    assert reply.await_args.kwargs["billing_paused"] is True


# -------------------------------------------------------------------------------- widget


async def _widget_chat(*, assistant: bool) -> tuple[Any, AsyncMock]:
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.conversation import surfaces
    from callobuzz_rag.widget.security import LimitResult
    from tests.unit.test_widget import SITE, _client, _key, _session, _settings, _use_session

    app = create_app()
    app.state.settings = _settings()
    app.state.widget_rate_limiter = SimpleNamespace(check=AsyncMock(return_value=LimitResult(True)))
    corpus = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        qdrant_collection="c",
        retrieval_config=None,
        llm_config=None,
        prompt_config=None,
    )
    _use_session(app, _session(_key(corpus.id), corpus))
    reply = AsyncMock(return_value=MagicMock())
    runtime = SimpleNamespace(config=SimpleNamespace(enabled=True)) if assistant else None
    with (
        quota(None),
        state(PAUSED),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch.object(surfaces, "widget_reply", reply),
        patch("callobuzz_rag.api.routes.widget._build_orchestrator", MagicMock()),
    ):
        async with await _client(app) as c:
            res = await c.post(
                "/api/v1/widget/chat",
                json={"message": "Can I return this?"},
                headers={"X-Widget-Key": "pk_x", "Origin": SITE},
            )
    return res, reply


async def test_widget_without_the_assistant_is_a_polite_409_that_never_says_why() -> None:
    res, reply = await _widget_chat(assistant=False)
    assert res.status_code == 409, res.text
    assert res.json() == {
        "detail": "This assistant is not available right now.",
        "code": "assistant_disabled",
    }
    assert not any(w in res.text.lower() for w in FORBIDDEN_WORDS)
    reply.assert_not_awaited()


@pytest.mark.assistant_on
async def test_widget_with_the_assistant_reaches_the_director_paused() -> None:
    _, reply = await _widget_chat(assistant=True)
    assert reply.await_args.kwargs["billing_paused"] is True
