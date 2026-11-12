"""The post-sync COB eval: stores a summary, never touches the sync it follows."""

from datetime import UTC, datetime

import pytest

from callobuzz_rag.cob.eval.items import EvalItem
from callobuzz_rag.cob.eval.offline import Bm25Index
from callobuzz_rag.cob.eval.synced import (
    Search,
    SyncedHit,
    evaluate_after_sync,
    run_synced_eval,
)
from callobuzz_rag.core.config import Settings
from tests.unit.test_cob_sync import Env, env  # noqa: F401  (the fixture)

NOW = datetime(2026, 10, 4, 9, 30, tzinfo=UTC)


def _items() -> list[EvalItem]:
    return [
        EvalItem.model_validate(
            {
                "id": "synced-hit-001",
                "role": "owner",
                "question": "How do I paste the snippet to embed the widget?",
                "expect": {
                    "behaviour": "answer",
                    "slug": "screens/widget",
                    "anchor": "embed-the-widget",
                },
            }
        ),
        EvalItem.model_validate(
            {
                "id": "synced-hi-001",
                "role": "owner",
                "language": "hi-Latn",
                "question": "snippet kahan paste karun",
                "translation_en": "Where do I paste the snippet to embed the widget?",
                "expect": {"behaviour": "answer", "slug": "screens/widget"},
            }
        ),
        EvalItem.model_validate(
            {
                "id": "synced-neg-001",
                "role": "owner",
                "question": "What do I set RAG_WIDGET_TRUST_PROXY to on the server?",
                "expect": {
                    "behaviour": "admin_managed",
                    "slug": "screens/widget",
                    "anchor": "operator-notes",
                    "must_not_include": ["RAG_WIDGET_TRUST_PROXY"],
                },
            }
        ),
    ]


def _real_like_search(environment: Env) -> Search:
    bm25 = Bm25Index(environment.index)

    async def search(query: str, audiences: list[str], k: int) -> list[SyncedHit]:
        return [SyncedHit(h.slug, h.anchor, h.text) for h in bm25.search(query, audiences, k)]

    return search


async def test_eval_after_sync_stores_the_summary(env: Env) -> None:  # noqa: F811
    await env.sync()
    search = _real_like_search(env)
    summary = await evaluate_after_sync(
        env.sf, env.index, lambda _s, _c: search, items=_items(), now=NOW
    )
    stored = env.status().eval
    assert stored == summary
    assert stored is not None
    assert set(stored) == {
        "at",
        "version",
        "items",
        "hit_at_5",
        "hit_at_5_indic",
        "role_leaks",
        "negatives_failed",
        "passed",
    }
    assert stored["version"] == env.index.version and stored["items"] == 3
    assert stored["hit_at_5"] == 1.0 and stored["hit_at_5_indic"] == 1.0
    assert stored["role_leaks"] == 0 and stored["negatives_failed"] == 0
    assert stored["passed"] is True and stored["at"] == NOW.isoformat()


async def test_a_failing_eval_leaves_the_sync_alone(env: Env) -> None:  # noqa: F811
    await env.sync()

    async def nothing(query: str, audiences: list[str], k: int) -> list[SyncedHit]:
        return []

    summary = await evaluate_after_sync(env.sf, env.index, lambda _s, _c: nothing, items=_items())
    assert summary["passed"] is False and summary["hit_at_5"] == 0.0
    # The probe found nothing either, so the negative item counts as failed too.
    assert summary["negatives_failed"] == 1
    status = env.status()
    assert status.sync_state == "idle" and status.docs_version == env.index.version
    assert status.eval == summary


async def test_an_eval_that_crashes_is_recorded_not_raised(env: Env) -> None:  # noqa: F811
    await env.sync()

    async def broken(query: str, audiences: list[str], k: int) -> list[SyncedHit]:
        raise RuntimeError("the vector store went away")

    summary = await evaluate_after_sync(env.sf, env.index, lambda _s, _c: broken, items=_items())
    assert summary["passed"] is False and "vector store went away" in summary["error"]
    assert env.status().eval == summary and env.status().sync_state == "idle"


async def test_text_above_the_role_counts_as_a_leak(env: Env) -> None:  # noqa: F811
    await env.sync()
    honest = _real_like_search(env)

    async def leaky(query: str, audiences: list[str], k: int) -> list[SyncedHit]:
        # Ignores the audience filter, as a broken filter would.
        return await honest(query, ["admin", "customer", "public"], k)

    summary = await run_synced_eval(leaky, _items(), index=env.index, now=NOW)
    assert summary["role_leaks"] >= 1 and summary["negatives_failed"] == 1
    assert summary["passed"] is False


async def test_an_empty_item_list_passes_vacuously(env: Env) -> None:  # noqa: F811
    summary = await run_synced_eval(_real_like_search(env), [], index=env.index, now=NOW)
    assert summary["items"] == 0 and summary["hit_at_5"] == 1.0 and summary["passed"] is True


async def test_system_search_asks_the_real_retrieval_with_the_audience_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types
    import uuid

    from callobuzz_rag.cob.eval.synced import system_search

    calls: list[dict[str, object]] = []
    platform_doc = uuid.uuid4()

    class FakeOrchestrator:
        def __init__(self, **kwargs: object) -> None:
            calls.append({"init": kwargs})

        async def run_retrieval_pipeline(
            self, text: str, corpus_id: object, **kw: object
        ) -> object:
            calls.append({"text": text, "corpus_id": corpus_id, **kw})
            chunk = types.SimpleNamespace(
                external_id="system:cob/screens/widget#intro", text="T", document_id=None
            )
            stale = types.SimpleNamespace(
                external_id="system:other/old", text="X", document_id=platform_doc
            )
            page = types.SimpleNamespace(
                external_id=None, text="P", document_id=platform_doc, audience="admin"
            )
            assembled = types.SimpleNamespace(cited_chunks=[chunk, stale, page])
            return types.SimpleNamespace(assembled=assembled)

    monkeypatch.setattr("callobuzz_rag.retrieval.orchestrator.QueryOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(
        "callobuzz_rag.embeddings.factory.create_embedding_provider", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        "callobuzz_rag.embeddings.factory.embedder_for_corpus", lambda *a, **k: object()
    )
    corpus = types.SimpleNamespace(id=uuid.uuid4(), qdrant_collection="cob_docs")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    search = system_search(object(), settings, corpus, object())  # type: ignore[arg-type]
    hits = await search("embed the widget", ["customer", "public"], 5)
    # A platform page keeps the audience it was returned with; a stale built-in id is dropped.
    assert hits == [
        SyncedHit("screens/widget", "intro", "T"),
        SyncedHit(f"platform/{platform_doc}", "", "P", "admin"),
    ]
    call = calls[1]
    assert call["metadata_filters"] == {"audience": ["customer", "public"]}
    assert call["enable_hyde"] is False and call["enable_reranking"] is False
    assert call["final_k"] == 5 and call["corpus_id"] == corpus.id
    # The evaluation must never be able to call a language model.
    llm = calls[0]["init"]["llm_provider"]  # type: ignore[index]
    with pytest.raises(RuntimeError, match="no model calls"):
        llm.complete  # noqa: B018


async def test_system_search_closes_the_embedding_clients_it_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types
    import uuid

    from callobuzz_rag.cob.eval.synced import system_search

    closed: list[str] = []

    def client(name: str) -> object:
        async def close() -> None:
            closed.append(name)

        return types.SimpleNamespace(close=close)

    monkeypatch.setattr("callobuzz_rag.retrieval.orchestrator.QueryOrchestrator", lambda **k: None)
    monkeypatch.setattr(
        "callobuzz_rag.embeddings.factory.create_embedding_provider",
        lambda *a, **k: client("default"),
    )
    monkeypatch.setattr(
        "callobuzz_rag.embeddings.factory.embedder_for_corpus",
        lambda *a, **k: client("corpus"),
    )
    corpus = types.SimpleNamespace(id=uuid.uuid4(), qdrant_collection="cob_docs")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    search = system_search(object(), settings, corpus, object())  # type: ignore[arg-type]
    await search.aclose()
    assert closed == ["default", "corpus"]


async def test_the_eval_closes_the_search_it_was_given(env: Env) -> None:  # noqa: F811
    await env.sync()
    honest = _real_like_search(env)
    closed: list[bool] = []

    class Closing:
        async def __call__(self, query: str, audiences: list[str], k: int) -> list[SyncedHit]:
            return await honest(query, audiences, k)

        async def aclose(self) -> None:
            closed.append(True)

    closing = Closing()
    await evaluate_after_sync(env.sf, env.index, lambda _s, _c: closing, items=_items(), now=NOW)
    assert closed == [True]
