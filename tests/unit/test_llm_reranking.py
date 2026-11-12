"""Unit tests for the LLM reranker and the local-fallback wrapper.

The LLM is faked with a real class implementing `LLMProvider`, not a MagicMock.
A MagicMock would answer any attribute, which is how a previous defect in this
codebase (`QueryOrchestrator.search`, a method that did not exist) survived its
own test — the fake here can only do what the interface declares.

Every reply body below is generated text as far as the provider is concerned, so
the tests are mostly about what happens when the model answers badly: that is the
normal case for an LLM, not the edge case.
"""

import logging

import pytest

from callobuzz_rag.core.exceptions import RerankError
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.llm.models import LLMMessage, LLMResponse, LLMUsage
from callobuzz_rag.retrieval.reranking.base import RerankProvider, RerankResult
from callobuzz_rag.retrieval.reranking.llm import (
    FallbackRerankProvider,
    LLMRerankProvider,
)

DOCS = [
    "Fragile items are wrapped in bubble wrap and double-boxed.",
    "Vendor payment terms are net 30 from invoice date.",
    "Heavy orders over 20 kg use the reinforced box type.",
    "Office hours are 9am to 6pm Monday to Friday.",
]


class FakeLLM(LLMProvider):
    """An LLMProvider that returns a scripted reply and records its calls."""

    def __init__(
        self,
        reply: str = "",
        *,
        has_credentials: bool = True,
        fast_model: str | None = "cheap/model",
        raises: Exception | None = None,
    ) -> None:
        self._reply = reply
        self._has_credentials = has_credentials
        self._fast_model = fast_model
        self._raises = raises
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("reranking must use complete_json, not complete")

    def complete_stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("reranking must not stream")

    async def complete_json(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "model": model, "temperature": temperature})
        if self._raises is not None:
            raise self._raises
        return LLMResponse(content=self._reply, model=model or "test", usage=LLMUsage())

    async def health_check(self) -> bool:
        return True

    @property
    def fast_model(self) -> str | None:
        return self._fast_model

    @property
    def has_credentials(self) -> bool:
        return self._has_credentials


class RecordingReranker(RerankProvider):
    """A local stand-in that records whether it was reached."""

    def __init__(self) -> None:
        self.called = False

    async def rerank(self, query: str, documents: list[str], top_k: int = 10) -> list[RerankResult]:
        self.called = True
        return [RerankResult(index=0, score=0.5, text=documents[0])]


class TestScoredRanking:
    @pytest.mark.asyncio
    async def test_orders_by_the_models_ranking(self) -> None:
        llm = FakeLLM('{"ranking":[{"index":2,"score":0.91},{"index":0,"score":0.74}]}')
        results = await LLMRerankProvider(llm).rerank("which box for a heavy order", DOCS)

        assert [r.index for r in results] == [2, 0]
        assert [r.score for r in results] == [0.91, 0.74]
        # The text must travel with the result — downstream reads it rather than
        # re-indexing the caller's list.
        assert results[0].text == DOCS[2]

    @pytest.mark.asyncio
    async def test_top_k_truncates_after_ranking(self) -> None:
        llm = FakeLLM(
            '{"ranking":[{"index":2,"score":0.9},{"index":0,"score":0.8},'
            '{"index":1,"score":0.2},{"index":3,"score":0.1}]}'
        )
        results = await LLMRerankProvider(llm).rerank("packing", DOCS, top_k=2)

        assert [r.index for r in results] == [2, 0]

    @pytest.mark.asyncio
    async def test_a_bare_array_is_accepted_as_the_ranking(self) -> None:
        """Some models drop the wrapper object. The ordering is still usable."""
        llm = FakeLLM('[{"index":1,"score":0.6},{"index":3,"score":0.4}]')
        results = await LLMRerankProvider(llm).rerank("terms", DOCS)

        assert [r.index for r in results] == [1, 3]

    @pytest.mark.asyncio
    async def test_scores_are_clamped_not_discarded(self) -> None:
        """A model answering 1.2 meant 'very relevant'; losing that is worse."""
        llm = FakeLLM('{"ranking":[{"index":0,"score":1.2},{"index":1,"score":-0.5}]}')
        results = await LLMRerankProvider(llm).rerank("q", DOCS)

        assert [r.score for r in results] == [1.0, 0.0]

    @pytest.mark.asyncio
    async def test_uses_fast_model_and_zero_temperature(self) -> None:
        """Ranking is a judgement call, not the answer model's job."""
        llm = FakeLLM('{"ranking":[{"index":0,"score":1.0}]}')
        await LLMRerankProvider(llm).rerank("q", DOCS)

        assert llm.calls[0]["model"] == "cheap/model"
        assert llm.calls[0]["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_explicit_model_overrides_fast_model(self) -> None:
        llm = FakeLLM('{"ranking":[{"index":0,"score":1.0}]}')
        await LLMRerankProvider(llm, model="chosen/model").rerank("q", DOCS)

        assert llm.calls[0]["model"] == "chosen/model"

    @pytest.mark.asyncio
    async def test_candidates_are_numbered_in_the_prompt(self) -> None:
        """The indices the model answers with have to mean something."""
        llm = FakeLLM('{"ranking":[{"index":0,"score":1.0}]}')
        await LLMRerankProvider(llm).rerank("q", DOCS)

        messages = llm.calls[0]["messages"]
        user = next(m for m in messages if m.role == "user")  # type: ignore[union-attr]
        assert "[0] Fragile items" in user.content
        assert "[3] Office hours" in user.content


class TestOrderingWithoutScores:
    @pytest.mark.asyncio
    async def test_bare_indices_get_positional_scores(self) -> None:
        """The measured behaviour of real models: an ordering, no scores.

        The ordering must survive, because it is the whole point of the call,
        and `score` must still be a usable number since it is written onto
        `SearchResult.score` when pipeline ordering is on.
        """
        llm = FakeLLM('{"ranking":[2,0,1,3]}')
        results = await LLMRerankProvider(llm).rerank("q", DOCS)

        assert [r.index for r in results] == [2, 0, 1, 3]
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 < s <= 1.0 for s in scores)

    @pytest.mark.asyncio
    async def test_missing_score_field_falls_back_to_position(self) -> None:
        llm = FakeLLM('{"ranking":[{"index":1},{"index":0}]}')
        results = await LLMRerankProvider(llm).rerank("q", DOCS)

        assert [r.index for r in results] == [1, 0]
        assert results[0].score > results[1].score

    @pytest.mark.asyncio
    async def test_derivation_is_logged_not_hidden(self, caplog: pytest.LogCaptureFixture) -> None:
        llm = FakeLLM('{"ranking":[1,0]}')
        with caplog.at_level(logging.INFO):
            await LLMRerankProvider(llm).rerank("q", DOCS)

        assert any("derived from position" in r.message for r in caplog.records)


class TestBadReplies:
    @pytest.mark.asyncio
    async def test_out_of_range_index_is_dropped_and_the_rest_kept(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A hallucinated index must not IndexError deep in the pipeline."""
        llm = FakeLLM('{"ranking":[{"index":99,"score":1.0},{"index":1,"score":0.5}]}')
        with caplog.at_level(logging.WARNING):
            results = await LLMRerankProvider(llm).rerank("q", DOCS)

        assert [r.index for r in results] == [1]
        assert any("out-of-range" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_duplicate_index_is_deduplicated(self) -> None:
        """Two results pointing at one chunk would double it in the answer."""
        llm = FakeLLM(
            '{"ranking":[{"index":2,"score":0.9},{"index":2,"score":0.8},{"index":0,"score":0.7}]}'
        )
        results = await LLMRerankProvider(llm).rerank("q", DOCS)

        assert [r.index for r in results] == [2, 0]

    @pytest.mark.asyncio
    async def test_true_in_the_array_is_not_candidate_one(self) -> None:
        """`bool` is an `int` subclass in Python; a `true` is a bad reply."""
        llm = FakeLLM('{"ranking":[true,{"index":0,"score":0.9}]}')
        results = await LLMRerankProvider(llm).rerank("q", DOCS)

        assert [r.index for r in results] == [0]

    @pytest.mark.asyncio
    async def test_non_json_reply_raises_rerank_error(self) -> None:
        llm = FakeLLM("I ranked them for you!")
        with pytest.raises(RerankError):
            await LLMRerankProvider(llm).rerank("q", DOCS)

    @pytest.mark.asyncio
    async def test_json_that_is_not_a_ranking_raises(self) -> None:
        llm = FakeLLM('{"answer":"the blue box"}')
        with pytest.raises(RerankError):
            await LLMRerankProvider(llm).rerank("q", DOCS)

    @pytest.mark.asyncio
    async def test_every_entry_unusable_raises_rather_than_returning_empty(self) -> None:
        """Empty would read downstream as 'nothing was relevant', which is a lie."""
        llm = FakeLLM('{"ranking":[{"index":99},{"index":-4}]}')
        with pytest.raises(RerankError):
            await LLMRerankProvider(llm).rerank("q", DOCS)

    @pytest.mark.asyncio
    async def test_provider_error_becomes_rerank_error(self) -> None:
        llm = FakeLLM(raises=RuntimeError("402 payment required"))
        with pytest.raises(RerankError, match="402"):
            await LLMRerankProvider(llm).rerank("q", DOCS)


class TestCredentials:
    def test_usable_reflects_the_credential(self) -> None:
        assert LLMRerankProvider(FakeLLM()).usable is True
        assert LLMRerankProvider(FakeLLM(has_credentials=False)).usable is False

    @pytest.mark.asyncio
    async def test_no_credentials_raises_without_calling_out(self) -> None:
        llm = FakeLLM(has_credentials=False)
        with pytest.raises(RerankError, match="credentials"):
            await LLMRerankProvider(llm).rerank("q", DOCS)

        assert llm.calls == []

    @pytest.mark.asyncio
    async def test_no_documents_short_circuits(self) -> None:
        llm = FakeLLM()
        assert await LLMRerankProvider(llm).rerank("q", []) == []
        assert llm.calls == []


class TestFallback:
    @pytest.mark.asyncio
    async def test_primary_success_never_reaches_secondary(self) -> None:
        local = RecordingReranker()
        primary = LLMRerankProvider(FakeLLM('{"ranking":[{"index":2,"score":0.9}]}'))
        results = await FallbackRerankProvider(primary, local).rerank("q", DOCS)

        assert [r.index for r in results] == [2]
        assert local.called is False

    @pytest.mark.asyncio
    async def test_primary_failure_falls_back_to_local(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The reason the paid toggle is safe to leave on.

        Without this the orchestrator's own `except` would swallow the failure
        and keep the raw fusion order — reranking silently off.
        """
        local = RecordingReranker()
        primary = LLMRerankProvider(FakeLLM(raises=RuntimeError("upstream 503")))
        with caplog.at_level(logging.WARNING):
            results = await FallbackRerankProvider(primary, local).rerank("q", DOCS)

        assert local.called is True
        assert [r.index for r in results] == [0]
        assert any("falling back to local" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_missing_credentials_falls_back_too(self) -> None:
        local = RecordingReranker()
        primary = LLMRerankProvider(FakeLLM(has_credentials=False))
        await FallbackRerankProvider(primary, local).rerank("q", DOCS)

        assert local.called is True


class TestSelection:
    """Which reranker a request actually gets.

    `_select_reranker` is the toggle. Everything above proves the paid provider
    works; this proves it is reached only when the corpus asked for it and a
    credential exists, and that the free one is always the floor.
    """

    @staticmethod
    def _resolved(**corpus_config: object):  # type: ignore[no-untyped-def]
        from callobuzz_rag.core.config import Settings
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = Settings(
            secret_key="x" * 32,
            jwt_secret_key="y" * 32,
            admin_api_key="z" * 32,
        )
        return ConfigResolver(settings).resolve_retrieval(dict(corpus_config))

    def test_local_is_returned_untouched_by_default(self) -> None:
        from callobuzz_rag.api.routes.query import _select_reranker

        local = RecordingReranker()
        got = _select_reranker(local, FakeLLM(), self._resolved())
        assert got is local

    def test_no_resolved_config_falls_back_to_local(self) -> None:
        """Defensive: a caller that forgets the kwarg must not start spending."""
        from callobuzz_rag.api.routes.query import _select_reranker

        local = RecordingReranker()
        assert _select_reranker(local, FakeLLM(), None) is local

    def test_corpus_asking_for_llm_gets_it_wrapped_in_local(self) -> None:
        from callobuzz_rag.api.routes.query import _select_reranker
        from callobuzz_rag.retrieval.reranking.llm import (
            FallbackRerankProvider,
            LLMRerankProvider,
        )

        local = RecordingReranker()
        got = _select_reranker(local, FakeLLM(), self._resolved(rerank_provider="llm"))

        assert isinstance(got, FallbackRerankProvider)
        assert isinstance(got._primary, LLMRerankProvider)
        assert got._secondary is local

    def test_llm_without_a_credential_returns_local(self) -> None:
        """A paid reranker that cannot run must not present as 'reranking off'."""
        from callobuzz_rag.api.routes.query import _select_reranker

        local = RecordingReranker()
        got = _select_reranker(
            local,
            FakeLLM(has_credentials=False),
            self._resolved(rerank_provider="llm"),
        )
        assert got is local

    def test_llm_with_no_local_available_is_used_bare(self) -> None:
        """A box where the cross-encoder failed to load still gets reranking."""
        from callobuzz_rag.api.routes.query import _select_reranker
        from callobuzz_rag.retrieval.reranking.llm import LLMRerankProvider

        got = _select_reranker(None, FakeLLM(), self._resolved(rerank_provider="llm"))
        assert isinstance(got, LLMRerankProvider)

    def test_no_reranker_anywhere_stays_none(self) -> None:
        from callobuzz_rag.api.routes.query import _select_reranker

        got = _select_reranker(
            None, FakeLLM(has_credentials=False), self._resolved(rerank_provider="llm")
        )
        assert got is None

    def test_the_corpus_rerank_model_reaches_the_provider(self) -> None:
        from callobuzz_rag.api.routes.query import _select_reranker

        got = _select_reranker(
            None,
            FakeLLM(),
            self._resolved(rerank_provider="llm", rerank_llm_model="chosen/ranker"),
        )
        assert got._model == "chosen/ranker"  # type: ignore[attr-defined]

    def test_a_junk_provider_value_does_not_start_spending(self) -> None:
        """The resolver normalises; this pins that the selector agrees."""
        from callobuzz_rag.api.routes.query import _select_reranker

        local = RecordingReranker()
        got = _select_reranker(local, FakeLLM(), self._resolved(rerank_provider="hosted"))
        assert got is local
