"""Every answering route checks the monthly question cap before any work (accounts A6).

`/query` (a capped tenant's cached answer is refused too, unless its plan does not count cached
answers; see the last tests),
`/query/stream`, conversation messages and widget chat refuse a tenant at its cap with
429 `question_cap_reached`; the admin is never capped; search-only `/query/retrieve` is
never checked. The cap's own logic is in test_caps.py.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.accounts import caps
from tests.unit.scope_harness import call, scoped_app

CACHED = {
    "answer": "From the cache",
    "citations": [],
    "confidence": 1.0,
    "query_id": str(uuid.uuid4()),
}


@contextmanager
def quota(value: tuple[int, int] | None) -> Iterator[AsyncMock]:
    """The tenant's (monthly limit, used this month); None = unlimited."""
    from callobuzz_rag.billing.pricing import PricingFields, PricingOverrides, combine

    mock = AsyncMock(return_value=value)
    no_pricing = combine(PricingFields(), PricingOverrides(), currency="INR", exempt=False)
    with (
        patch.object(caps, "question_quota", mock),
        patch(
            "callobuzz_rag.billing.pricing.effective_pricing", AsyncMock(return_value=no_pricing)
        ),
        patch(
            "callobuzz_rag.accounts.settings_store.get_engine_settings",
            AsyncMock(side_effect=RuntimeError("no branding in this test")),
        ),
    ):
        yield mock


@contextmanager
def cached_answer() -> Iterator[MagicMock]:
    """The corpus has the query cache on and an answer cached for any question."""
    cache = MagicMock()
    cache.get = AsyncMock(return_value=CACHED)
    resolver = MagicMock()
    resolver.resolve_retrieval.return_value = SimpleNamespace(cache_enabled=True)
    with (
        patch("callobuzz_rag.api.routes.query._get_query_cache", return_value=cache),
        patch("callobuzz_rag.api.routes.query._get_config_resolver", return_value=resolver),
    ):
        yield cache


def _assert_capped(res: Any) -> None:
    assert res.status_code == 429, res.text
    body = res.json()
    assert body["code"] == "question_cap_reached"
    assert body["limit"] == 2 and body["used"] == 2
    assert body["resets_at"].endswith("T00:00:00Z")
    assert int(res.headers["Retry-After"]) >= 1


async def test_query_refuses_a_cached_answer_at_the_cap() -> None:
    with scoped_app() as (app, world), quota((2, 2)) as q, cached_answer():
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    _assert_capped(res)
    assert q.await_args.args[1] == world.tenant_a.id  # the corpus's tenant


async def test_query_under_the_cap_answers() -> None:
    with scoped_app() as (app, _), quota((2, 1)), cached_answer():
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 200, res.text
    assert res.json()["answer"] == "From the cache"


@pytest.mark.parametrize("key", ["tk_a", "owner_a", "member_a"])
async def test_every_tenant_principal_is_capped(key: str) -> None:
    with scoped_app() as (app, world), quota((2, 2)), cached_answer():
        res = await call(
            app,
            key,
            "POST",
            "/api/v1/query",
            json={"query": "hi", "corpus_id": str(world.corpus_a.id)},
        )
    _assert_capped(res)


@pytest.mark.parametrize("key", ["admin", "sa"])
async def test_the_admin_is_not_capped(key: str) -> None:
    with scoped_app() as (app, world), quota((2, 50)) as q, cached_answer():
        res = await call(
            app,
            key,
            "POST",
            "/api/v1/query",
            json={"query": "hi", "corpus_id": str(world.corpus_a.id)},
        )
    assert res.status_code == 200, res.text
    q.assert_not_awaited()


async def test_stream_is_capped() -> None:
    with scoped_app() as (app, _), quota((2, 2)):
        res = await call(app, "ck_a", "POST", "/api/v1/query/stream", json={"query": "hi"})
    _assert_capped(res)


async def test_conversation_message_is_capped() -> None:
    llm = MagicMock()
    with (
        scoped_app() as (app, world),
        quota((2, 2)),
        patch("callobuzz_rag.llm.litellm_provider.LiteLLMProvider.from_resolved_config", llm),
    ):
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/conversations/{uuid.uuid4()}/messages",
            json={"content": "hi"},
        )
    _assert_capped(res)
    llm.assert_not_called()  # before building the provider


async def test_retrieve_is_never_capped() -> None:
    with scoped_app() as (app, _), quota((2, 2)) as q:
        res = await call(app, "ck_a", "POST", "/api/v1/query/retrieve", json={"query": "hi"})
    assert res.status_code != 429
    q.assert_not_awaited()


async def test_widget_chat_is_capped_politely() -> None:
    from callobuzz_rag.api.app import create_app
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
    build = MagicMock()
    with quota((2, 2)) as q, patch("callobuzz_rag.api.routes.widget._build_orchestrator", build):
        async with await _client(app) as c:
            res = await c.post(
                "/api/v1/widget/chat",
                json={"message": "Can I return this?"},
                headers={"X-Widget-Key": "pk_x", "Origin": SITE},
            )
    assert res.status_code == 429, res.text
    assert res.json() == {
        "detail": "This assistant is not available right now.",
        "code": "question_cap_reached",
    }
    assert int(res.headers["Retry-After"]) >= 1
    assert q.await_args.args[1] == corpus.tenant_id
    build.assert_not_called()


# ------------------------------------------------ plan setting "count cached answers"


@contextmanager
def counts_cached(value: bool) -> Iterator[AsyncMock]:
    """The tenant's plan counts (or not) answers served from the query cache."""
    mock = AsyncMock(return_value=caps.Limits(count_cached_answers=value))
    with patch.object(caps, "effective_limits", mock):
        yield mock


async def _cached_query(app: Any, key: str = "ck_a") -> tuple[Any, AsyncMock]:
    with patch("callobuzz_rag.api.routes.query.record_usage", AsyncMock()) as record:
        res = await call(app, key, "POST", "/api/v1/query", json={"query": "hi"})
    return res, record


async def test_a_cached_answer_counts_when_the_plan_says_so() -> None:
    with scoped_app() as (app, world), quota((2, 1)), cached_answer(), counts_cached(True):
        res, record = await _cached_query(app)
    assert res.status_code == 200 and res.json()["answer"] == "From the cache"
    record.assert_awaited_once()
    kwargs = record.await_args.kwargs
    assert kwargs["tenant_id"] == world.tenant_a.id
    assert kwargs["prompt_tokens"] == 0 and kwargs["completion_tokens"] == 0
    assert kwargs.get("requests", 1) == 1


async def test_a_cached_answer_is_capped_when_the_plan_counts_it() -> None:
    with scoped_app() as (app, _), quota((2, 2)), cached_answer(), counts_cached(True):
        res, record = await _cached_query(app)
    _assert_capped(res)
    record.assert_not_awaited()


async def test_a_cached_answer_is_free_when_the_plan_does_not_count_it() -> None:
    """Not counted, and served even at the cap: it costs the operator nothing."""
    with scoped_app() as (app, _), quota((2, 2)), cached_answer(), counts_cached(False):
        res, record = await _cached_query(app)
    assert res.status_code == 200 and res.json()["answer"] == "From the cache"
    record.assert_not_awaited()


async def test_a_cache_miss_is_still_capped_when_cached_answers_are_free() -> None:
    with scoped_app() as (app, _), quota((2, 2)), cached_answer() as cache, counts_cached(False):
        cache.get.return_value = None
        res, record = await _cached_query(app)
    _assert_capped(res)
    record.assert_not_awaited()


# ------------------------------------------------ the assistant (conversation director) is on


@contextmanager
def assistant_on() -> Iterator[tuple[AsyncMock, AsyncMock]]:
    """The knowledge base's assistant is on: the director answers, the cache is bypassed."""
    from callobuzz_rag.conversation import surfaces

    runtime = SimpleNamespace(config=SimpleNamespace(enabled=True))
    answer, stream = AsyncMock(), AsyncMock()
    with (
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch.object(surfaces, "answer_query", answer),
        patch.object(surfaces, "stream_query_response", stream),
    ):
        yield answer, stream


@pytest.mark.assistant_on
async def test_query_with_the_assistant_on_is_capped_before_any_work() -> None:
    with scoped_app() as (app, world), quota((2, 2)) as q, assistant_on() as (answer, _):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    _assert_capped(res)
    assert q.await_args.args[1] == world.tenant_a.id
    answer.assert_not_awaited()


@pytest.mark.assistant_on
async def test_query_with_the_assistant_on_under_the_cap_reaches_the_director() -> None:
    from callobuzz_rag.api.schemas.query import QueryResponse

    done = QueryResponse(
        answer="From the director",
        confidence=1.0,
        query_id=uuid.uuid4(),
        model_used="m",
        latency_ms=1,
    )
    with scoped_app() as (app, _), quota((2, 1)) as q, assistant_on() as (answer, _):
        answer.return_value = done
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 200, res.text
    assert res.json()["answer"] == "From the director"
    q.assert_awaited_once()  # once: the director branch is not also counted by the old path


@pytest.mark.assistant_on
async def test_the_assistant_bypasses_the_cache_and_never_counts_a_cached_answer() -> None:
    with (
        scoped_app() as (app, _),
        quota((2, 1)),
        cached_answer() as cache,
        assistant_on() as (a, _),
    ):
        from callobuzz_rag.api.schemas.query import QueryResponse

        a.return_value = QueryResponse(
            answer="d", confidence=1.0, query_id=uuid.uuid4(), model_used="m", latency_ms=1
        )
        await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    cache.get.assert_not_awaited()


@pytest.mark.assistant_on
async def test_stream_with_the_assistant_on_is_capped() -> None:
    with scoped_app() as (app, _), quota((2, 2)), assistant_on() as (_, stream):
        res = await call(app, "ck_a", "POST", "/api/v1/query/stream", json={"query": "hi"})
    _assert_capped(res)
    stream.assert_not_awaited()


@pytest.mark.assistant_on
async def test_conversation_message_with_the_assistant_on_is_capped_before_it_answers() -> None:
    from callobuzz_rag.conversation import surfaces

    runtime = SimpleNamespace(config=SimpleNamespace(enabled=True))
    reply = AsyncMock()
    with (
        scoped_app() as (app, world),
        quota((2, 2)),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)) as lookup,
        patch.object(surfaces, "conversation_reply", reply),
    ):
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/conversations/{uuid.uuid4()}/messages",
            json={"content": "hi"},
        )
    _assert_capped(res)
    lookup.assert_awaited()  # TV1 human: looked up first, to let a request for a person by
    reply.assert_not_awaited()
