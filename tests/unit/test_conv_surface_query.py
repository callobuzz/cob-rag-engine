"""C1: /query and /query/stream with the assistant on, off, previewing and debugging."""

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.assistant import AssistantRequestOptions
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.conversation.envelope import DoneEvent, MetaEvent, TokenEvent
from callobuzz_rag.conversation.store import RuntimeConfig
from callobuzz_rag.conversation.types import Route, Script, TurnLabel
from callobuzz_rag.core.config import Settings
from tests.unit.conversation_helpers import make_config

pytestmark = [pytest.mark.usefixtures("unlimited_plan"), pytest.mark.assistant_on]
ADMIN = {"X-API-Key": "test-admin-key"}
QID = uuid.uuid4()


@pytest.fixture
def corpus() -> SimpleNamespace:
    cid = uuid.uuid4()
    return SimpleNamespace(
        id=cid,
        tenant_id=uuid.uuid4(),
        qdrant_collection="corpus_x",
        retrieval_config=None,
        llm_config=None,
        prompt_config=None,
        applied_embedding={"model": "BAAI/bge-m3"},
    )


@pytest.fixture
def app(corpus: SimpleNamespace) -> Any:
    application = create_app()
    application.state.settings = Settings(
        _env_file=None, admin_api_key="test-admin-key", llm_api_key="test"
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=corpus)
    from callobuzz_rag.api.deps import get_session

    async def override() -> Any:
        yield session

    application.dependency_overrides[get_session] = override
    return application


def _done(**kw: Any) -> DoneEvent:
    base: dict[str, Any] = {
        "query_id": QID,
        "label": TurnLabel.BUSINESS_QUESTION,
        "route": Route.GROUNDED,
        "language": "en",
        "cta": None,
        "timings": {"search": 12},
        "claims_removed": 0,
        "model": "m",
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "cost_usd": 0.001,
            "cost_known": True,
            "model_calls": 2,
        },
        "latency_ms": 12,
        "text": "Home collection costs Rs 150 [1].",
        "message_id": None,
        "conversation_id": None,
        "sources": [
            {
                "index": 1,
                "chunk_id": str(uuid.uuid4()),
                "document_id": None,
                "external_id": None,
                "file_name": "home.pdf",
                "section": "Charges",
                "page": 1,
                "text": "Rs 150",
                "score": 0.9,
                "score_breakdown": None,
            }
        ],
        "assistant_version": 3,
    }
    base.update(kw)
    return DoneEvent(**base)


class FakeDirector:
    def __init__(self, done: DoneEvent) -> None:
        self.done = done
        self.turns: list[Any] = []

    async def run(self, turn: Any) -> DoneEvent:
        self.turns.append(turn)
        return self.done

    async def run_stream(self, turn: Any):  # type: ignore[no-untyped-def]
        self.turns.append(turn)
        yield MetaEvent(
            query_id=QID,
            conversation_id=None,
            session=None,
            label=TurnLabel.BUSINESS_QUESTION,
            route=Route.GROUNDED,
            language="en",
            script=Script.LATIN,
            assistant_version=3,
        )
        yield TokenEvent(content="Hi")
        yield self.done

    async def aclose(self) -> None:
        return None


def _runtime() -> RuntimeConfig:
    return RuntimeConfig(config=make_config(), version=3, draft_revision=None, enabled=True)


async def _post(
    app: Any, path: str, body: dict[str, Any], director: FakeDirector, runtime: RuntimeConfig | None
) -> Any:
    with (
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch.object(surfaces, "build_director", MagicMock(return_value=director)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.post(path, json=body, headers=ADMIN)


async def test_query_json_has_envelope(app: Any, corpus: SimpleNamespace) -> None:
    director = FakeDirector(_done())
    res = await _post(
        app,
        "/api/v1/query",
        {"query": "home collection?", "corpus_id": str(corpus.id)},
        director,
        _runtime(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["answer"].startswith("Home collection") and body["label"] == "business_question"
    assert body["route"] == "grounded" and body["citations"][0]["file_name"] == "home.pdf"
    assert body["assistant_version"] == 3 and body["trace"] is None
    assert body["token_usage"]["cost_usd"] == 0.001 and body["token_usage"]["model_calls"] == 2


async def test_query_stream_meta_first(app: Any, corpus: SimpleNamespace) -> None:
    res = await _post(
        app,
        "/api/v1/query/stream",
        {"query": "hi", "corpus_id": str(corpus.id)},
        FakeDirector(_done()),
        _runtime(),
    )
    events = [b.split("\n")[0].removeprefix("event: ") for b in res.text.strip().split("\n\n")]
    assert events == ["meta", "token", "done"]
    done = json.loads(res.text.strip().split("\n\n")[-1].split("data: ", 1)[1])
    assert {"query_id", "usage", "label", "timings", "stage_timings_ms"} <= done.keys()


async def test_system_prompt_becomes_one_off_instructions(
    app: Any, corpus: SimpleNamespace
) -> None:
    director = FakeDirector(_done())
    await _post(
        app,
        "/api/v1/query",
        {"query": "q", "corpus_id": str(corpus.id), "system_prompt": "Answer in two lines."},
        director,
        _runtime(),
    )
    assert director.turns[0].one_off_instructions == "Answer in two lines."


async def test_preview_is_marked(app: Any, corpus: SimpleNamespace) -> None:
    director = FakeDirector(_done())
    await _post(
        app,
        "/api/v1/query",
        {"query": "q", "corpus_id": str(corpus.id), "assistant": {"use_draft": True}},
        director,
        _runtime(),
    )
    turn = director.turns[0]
    assert turn.is_preview and turn.channel.value == "playground"


async def test_simulate_needs_preview_or_debug(app: Any, corpus: SimpleNamespace) -> None:
    res = await _post(
        app,
        "/api/v1/query",
        {
            "query": "q",
            "corpus_id": str(corpus.id),
            "assistant": {"simulate": {"office_hours": "closed"}},
        },
        FakeDirector(_done()),
        _runtime(),
    )
    assert res.status_code == 422 and res.json()["code"] == "invalid_assistant_options"


def _request(role: str | None = None, permission: str | None = None) -> Any:
    state = SimpleNamespace(role=role)
    if permission is not None:
        state.api_key = SimpleNamespace(permission=permission)
    return SimpleNamespace(state=state)


def test_debug_requires_an_owner() -> None:
    with pytest.raises(APIError) as err:
        surfaces.authorize_assistant_options(
            _request("member"), AssistantRequestOptions(debug=True)
        )
    assert err.value.code == "owner_required"
    surfaces.authorize_assistant_options(_request("owner"), AssistantRequestOptions(debug=True))


@pytest.mark.parametrize(
    "opts",
    [
        {"use_draft": True},
        {"debug": True},
        {"use_draft": True, "simulate": {"language": "hi"}},
    ],
)
def test_a_read_key_cannot_pull_the_draft_or_the_trace(opts: dict[str, Any]) -> None:
    with pytest.raises(APIError) as err:
        surfaces.authorize_assistant_options(
            _request("owner", "read"), AssistantRequestOptions(**opts)
        )
    assert err.value.status_code == 403 and err.value.code == "key_permission_denied"
    assert err.value.extra == {"permission": "read", "required": "write"}


@pytest.mark.parametrize("permission", ["write", "full"])
def test_write_and_full_keys_may_preview_and_debug(permission: str) -> None:
    surfaces.authorize_assistant_options(
        _request("owner", permission), AssistantRequestOptions(use_draft=True, debug=True)
    )


def test_a_read_key_may_still_ask_and_continue_a_conversation() -> None:
    surfaces.authorize_assistant_options(
        _request("owner", "read"),
        AssistantRequestOptions(conversation_id=uuid.uuid4(), page_url="https://x.in/p"),
    )
    surfaces.authorize_assistant_options(_request("owner", "read"), None)


def test_the_admin_key_and_sessions_are_not_keys_with_a_permission() -> None:
    surfaces.authorize_assistant_options(
        _request("owner"), AssistantRequestOptions(use_draft=True, debug=True)
    )


async def test_assistant_off_keeps_the_old_path(app: Any, corpus: SimpleNamespace) -> None:
    director = FakeDirector(_done())
    with patch("callobuzz_rag.api.routes.query._build_orchestrator") as build:
        build.return_value.query = AsyncMock(
            return_value={
                "answer": "old",
                "citations": [],
                "confidence": 1.0,
                "query_id": QID,
                "model_used": "m",
                "latency_ms": 1,
                "token_usage": {},
            }
        )
        with patch("callobuzz_rag.api.routes.query.record_usage", AsyncMock()):
            res = await _post(
                app, "/api/v1/query", {"query": "q", "corpus_id": str(corpus.id)}, director, None
            )
    assert res.json()["answer"] == "old" and res.json()["label"] is None
    assert director.turns == []


async def test_retrieve_refuses_assistant_options(app: Any, corpus: SimpleNamespace) -> None:
    res = await _post(
        app,
        "/api/v1/query/retrieve",
        {"query": "q", "corpus_id": str(corpus.id), "assistant": {"use_draft": True}},
        FakeDirector(_done()),
        None,
    )
    assert res.status_code == 422


def test_director_for_worker_needs_no_request() -> None:
    settings = Settings(_env_file=None, llm_api_key="test")
    corpus = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        qdrant_collection="corpus_x",
        retrieval_config={"top_k": 20},
        llm_config=None,
        prompt_config=None,
        applied_embedding=None,
        embedding_config=None,
    )
    embedder = SimpleNamespace(close=AsyncMock())
    with (
        patch("callobuzz_rag.embeddings.factory.create_embedding_provider") as embed,
        patch("callobuzz_rag.vectordb.qdrant.QdrantVectorProvider") as qdrant,
    ):
        embed.return_value = embedder
        qdrant.return_value.close = AsyncMock()
        director = surfaces.director_for_worker(
            lambda: MagicMock(),  # type: ignore[arg-type,return-value]
            settings,
            corpus,  # type: ignore[arg-type]
            use_draft=True,
            retrieval_overrides={"top_k": 40},
            llm_override={"model": "experiment-model", "provider": "openrouter"},
        )
    # A worker task runs in its own event loop: the embedder is built uncached.
    assert embed.call_args.kwargs == {"cache": False}
    qdrant.assert_called_once_with(settings)
    assert director._deps.retrieval_kwargs["top_k"] == 40
    assert director._deps.model_name == "experiment-model"
    orch = director._deps.orchestrator_factory(MagicMock())
    assert orch._collection_name == "corpus_x"
    assert orch._settings.collapse_duplicates == settings.collapse_duplicates


async def test_a_worker_director_closes_what_it_opened() -> None:
    settings = Settings(_env_file=None, llm_api_key="test")
    corpus = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        qdrant_collection="corpus_x",
        retrieval_config=None,
        llm_config=None,
        prompt_config=None,
        applied_embedding=None,
        embedding_config=None,
    )
    embedder = SimpleNamespace(close=AsyncMock())
    with (
        patch("callobuzz_rag.embeddings.factory.create_embedding_provider", return_value=embedder),
        patch("callobuzz_rag.vectordb.qdrant.QdrantVectorProvider") as qdrant,
    ):
        qdrant.return_value.close = AsyncMock()
        director = surfaces.director_for_worker(
            lambda: MagicMock(),  # type: ignore[arg-type,return-value]
            settings,
            corpus,  # type: ignore[arg-type]
            use_draft=False,
        )
    await director.aclose()
    await director.aclose()  # idempotent
    embedder.close.assert_awaited_once()
    qdrant.return_value.close.assert_awaited_once()


def test_one_provider_carries_the_whole_turn() -> None:
    """The director's provider is the orchestrator's provider: one cost ledger per turn."""
    from callobuzz_rag.api.routes.query import _build_orchestrator

    request = MagicMock()
    request.app.state.settings = Settings(_env_file=None)
    request.app.state.embedder = MagicMock()
    request.app.state.context_assembler = MagicMock()
    request.app.state.reranker = None
    request.app.state.vector_store = MagicMock()
    resolved_llm = MagicMock()
    shared = MagicMock()
    corpus = SimpleNamespace(qdrant_collection="c", applied_embedding=None, embedding_config=None)
    with patch("callobuzz_rag.embeddings.factory.embedder_for_corpus", return_value=MagicMock()):
        orch = _build_orchestrator(
            request,
            MagicMock(),
            corpus,
            resolved_llm,
            MagicMock(),
            llm=shared,  # type: ignore[arg-type]
        )
    assert orch.llm is shared


def test_retrieval_kwargs_mirror_the_old_mapping() -> None:
    from callobuzz_rag.api.schemas.query import QueryOptions

    resolved = SimpleNamespace(
        top_k=20,
        final_k=5,
        hyde_enabled=False,
        query_classification_enabled=True,
        mmr_enabled=False,
        reranking_enabled=False,
        temporal_decay_enabled=False,
        hybrid_search_enabled=True,
        pipeline_order_enabled=True,
    )
    kw = surfaces.retrieval_kwargs(QueryOptions(top_k=40, search_mode="keyword"), resolved)  # type: ignore[arg-type]
    assert kw["top_k"] == 40 and kw["final_k"] == 5 and kw["keyword_only"] is True
    assert kw["enable_hybrid"] is False and kw["enable_pipeline_order"] is True
    assert "system_prompt" not in kw and "enable_validation" not in kw
    default = surfaces.retrieval_kwargs(None, resolved)  # type: ignore[arg-type]
    assert default["top_k"] == 20 and default["enable_hybrid"] is True
    assert default["keyword_only"] is False and default["category"] is None
