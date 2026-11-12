"""Docs search: retrieval only, filtered by role, never metered, 503 when it cannot run."""

from types import SimpleNamespace

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.cob import docs_service
from callobuzz_rag.cob.kb import load_docs_index
from callobuzz_rag.cob.types import CobRole, allowed_audiences
from tests.unit.cob_harness import FakeRetriever

OWNER = allowed_audiences(CobRole.OWNER)


async def test_search_uses_the_audience_filter_and_merges_title_matches_first(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    retriever = FakeRetriever()

    async def system(session):  # type: ignore[no-untyped-def]
        return SimpleNamespace(id="c1")

    monkeypatch.setattr("callobuzz_rag.cob.system.get_system_corpus", system)
    monkeypatch.setattr("callobuzz_rag.cob.orchestrator.build_retriever", lambda *a, **k: retriever)
    out = await docs_service.search(
        None, object(), "website widget", OWNER, limit=10, index=load_docs_index()
    )
    call = retriever.calls[-1]
    assert call["metadata_filters"] == {"audience": ["customer", "public"]}
    assert call["enable_hyde"] is False and call["enable_reranking"] is False
    assert out.items[0].slug == "screens/widget" and out.items[0].score == 1.0
    slugs = [i.slug for i in out.items]
    assert "screens/engine" not in slugs and len(set(slugs)) == len(slugs)


async def test_a_failing_search_is_503(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def boom(session):  # type: ignore[no-untyped-def]
        raise RuntimeError("qdrant down")

    monkeypatch.setattr("callobuzz_rag.cob.system.get_system_corpus", boom)
    with pytest.raises(APIError) as caught:
        await docs_service.search(None, object(), "x", OWNER, limit=5, index=load_docs_index())
    assert caught.value.status_code == 503 and caught.value.code == "docs_search_unavailable"


async def test_service_post_filters_hidden_and_meters_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A retriever that ignores the audience filter still cannot leak admin material."""
    from callobuzz_rag.cob.kb import RECORD_PREFIX

    index = load_docs_index()
    overview = index.pages["screens/overview"]
    admin_section = next(s for s in overview.sections if s.anchor and s.audience.value == "admin")
    hits = [("screens/engine", ""), ("screens/overview", admin_section.anchor)]

    class Leaky:
        async def run_retrieval_pipeline(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            chunks = [
                SimpleNamespace(external_id=f"{RECORD_PREFIX}{s}#{a}", score=0.9, text="x")
                for s, a in hits
            ]
            return SimpleNamespace(assembled=SimpleNamespace(cited_chunks=chunks))

    async def system(session):  # type: ignore[no-untyped-def]
        return SimpleNamespace(id="c1")

    def fail(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("docs search must not meter usage")

    monkeypatch.setattr("callobuzz_rag.cob.system.get_system_corpus", system)
    monkeypatch.setattr("callobuzz_rag.cob.orchestrator.build_retriever", lambda *a, **k: Leaky())
    monkeypatch.setattr("callobuzz_rag.analytics.usage.record_usage", fail)
    monkeypatch.setattr("callobuzz_rag.analytics.usage.record_usage_new_session", fail)
    for role in (CobRole.OWNER, CobRole.MEMBER):
        out = await docs_service.search(
            None, object(), "zzzz-no-title-match", allowed_audiences(role), limit=10, index=index
        )
        keys = {(i.slug, i.anchor) for i in out.items}
        assert ("screens/engine", "") not in keys
        assert ("screens/overview", admin_section.anchor) not in keys
