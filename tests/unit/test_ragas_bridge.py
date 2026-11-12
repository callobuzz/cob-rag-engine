"""RAGAS runs on the engine's LLM and embeddings, with the engine's metric names.

It used to call `ragas.evaluate()` bare: RAGAS then wanted OPENAI_API_KEY (the
engine has none), context recall demanded a `reference` no sample had, the
precision metric's RAGAS name matched none of the engine's so it was dropped,
and the result was read with `.items()`, which RAGAS's result type lacks.
"""

import math
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ResolvedLLMConfig
from callobuzz_rag.core.exceptions import EvaluationError
from callobuzz_rag.embeddings.base import EmbeddingProvider, EmbeddingResult
from callobuzz_rag.evaluation.metrics import (
    ANSWER_RELEVANCY,
    CONTEXT_PRECISION,
    CONTEXT_RECALL,
    FAITHFULNESS,
)
from callobuzz_rag.evaluation.ragas_bridge import (
    RagasEmbeddings,
    aggregate_scores,
    build_ragas_llm,
)
from callobuzz_rag.evaluation.runner import (
    _RAGAS_METRIC_NAMES,
    EvaluationRunner,
    _run_ragas_evaluate,
)
from callobuzz_rag.llm.models import LLMResponse


def _llm_config(api_key: str = "k", base_url: str | None = None) -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        model="openai/gpt-4o-mini",
        api_key=api_key,
        base_url=base_url,
        temperature=0.0,
        max_tokens=256,
        provider="openai",
    )


SAMPLE = {
    "user_input": "What is RAG?",
    "response": "Retrieval-augmented generation.",
    "retrieved_contexts": ["RAG means retrieval-augmented generation."],
}


class _FakeEmbedder(EmbeddingProvider):
    def __init__(self) -> None:
        self.closed = False
        self.tasks: list[str] = []

    async def embed_texts(
        self, texts: list[str], task: str = "retrieval.passage"
    ) -> list[EmbeddingResult]:
        self.tasks.append(task)
        return [EmbeddingResult([float(len(t)), 1.0], "m", "v", 2) for t in texts]

    async def embed_query(self, text: str) -> EmbeddingResult:
        return (await self.embed_texts([text], task="retrieval.query"))[0]

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


def test_aggregate_scores_maps_names_and_skips_nan() -> None:
    rows = [
        {"faithfulness": 1.0, "llm_context_precision_without_reference": 0.5, "x": 3},
        {"faithfulness": 0.0, "llm_context_precision_without_reference": math.nan},
        {"faithfulness": math.nan, "answer_relevancy": math.nan},
    ]
    scores = aggregate_scores(rows, _RAGAS_METRIC_NAMES)
    assert scores == {FAITHFULNESS: 0.5, CONTEXT_PRECISION: 0.5}


def test_metric_names_cover_every_engine_metric() -> None:
    assert set(_RAGAS_METRIC_NAMES.values()) == {
        FAITHFULNESS,
        ANSWER_RELEVANCY,
        CONTEXT_PRECISION,
        CONTEXT_RECALL,
    }


async def test_llm_adapter_calls_engine_provider() -> None:
    from langchain_core.prompt_values import StringPromptValue

    llm = build_ragas_llm(_llm_config())
    complete = AsyncMock(
        return_value=LLMResponse(content='{"ok": 1}', model="m", finish_reason="stop")
    )
    with patch("callobuzz_rag.llm.litellm_provider.LiteLLMProvider.complete", complete):
        result = await llm.agenerate_text(StringPromptValue(text="judge this"), n=2)
    assert [g.text for g in result.generations[0]] == ['{"ok": 1}', '{"ok": 1}']
    assert complete.await_count == 2
    assert complete.await_args.args[0][0].content == "judge this"
    assert llm.is_finished(result)


async def test_llm_adapter_treats_truncation_as_unfinished() -> None:
    from langchain_core.outputs import Generation, LLMResult

    llm = build_ragas_llm(_llm_config())
    cut = LLMResult(
        generations=[[Generation(text="{", generation_info={"finish_reason": "length"})]]
    )
    assert not llm.is_finished(cut)


def test_llm_adapter_sync_path_works_inside_a_running_loop() -> None:
    import asyncio

    from langchain_core.prompt_values import StringPromptValue

    llm = build_ragas_llm(_llm_config())
    complete = AsyncMock(return_value=LLMResponse(content="yes", model="m"))

    async def _inside_loop() -> str:
        return str(llm.generate_text(StringPromptValue(text="q")).generations[0][0].text)

    with patch("callobuzz_rag.llm.litellm_provider.LiteLLMProvider.complete", complete):
        assert asyncio.run(_inside_loop()) == "yes"


async def test_embeddings_adapter_sync_and_async_and_closes() -> None:
    fake = _FakeEmbedder()
    with (
        patch("callobuzz_rag.embeddings.factory.create_embedding_provider", return_value=fake),
        RagasEmbeddings(Settings()) as emb,
    ):
        # Sync call from inside a running loop, as ResponseRelevancy does.
        assert emb.ragas.embed_query("abc") == [3.0, 1.0]
        assert emb.ragas.embed_documents(["a", "ab"]) == [[1.0, 1.0], [2.0, 1.0]]
        assert await emb.ragas.aembed_query("abcd") == [4.0, 1.0]
    assert fake.closed
    assert set(fake.tasks) == {"retrieval.query"}


def _fake_result(rows: list[dict[str, float]]) -> SimpleNamespace:
    return SimpleNamespace(scores=rows)


def test_run_ragas_evaluate_uses_engine_llm_and_skips_recall_without_reference() -> None:
    evaluate = MagicMock(
        return_value=_fake_result(
            [
                {
                    "faithfulness": 0.9,
                    "answer_relevancy": 0.8,
                    "llm_context_precision_without_reference": 0.7,
                }
            ]
        )
    )
    emb = MagicMock()
    emb.__enter__.return_value = SimpleNamespace(ragas="EMB")
    with (
        patch("ragas.evaluate", evaluate),
        patch("callobuzz_rag.evaluation.ragas_bridge.RagasEmbeddings", return_value=emb),
        patch("callobuzz_rag.evaluation.ragas_bridge.build_ragas_llm", return_value="LLM") as b,
    ):
        scores = _run_ragas_evaluate([SAMPLE], _llm_config(), Settings())

    assert scores == {FAITHFULNESS: 0.9, ANSWER_RELEVANCY: 0.8, CONTEXT_PRECISION: 0.7}
    kwargs = evaluate.call_args.kwargs
    assert kwargs["llm"] == "LLM" and kwargs["embeddings"] == "EMB"
    assert b.call_args.args[0].model == "openai/gpt-4o-mini"
    assert [m.name for m in kwargs["metrics"]] == [
        "faithfulness",
        "answer_relevancy",
        "llm_context_precision_without_reference",
    ]


def test_run_ragas_evaluate_adds_recall_when_every_sample_has_reference() -> None:
    evaluate = MagicMock(return_value=_fake_result([{"context_recall": 1.0}]))
    emb = MagicMock()
    emb.__enter__.return_value = SimpleNamespace(ragas="EMB")
    with (
        patch("ragas.evaluate", evaluate),
        patch("callobuzz_rag.evaluation.ragas_bridge.RagasEmbeddings", return_value=emb),
        patch("callobuzz_rag.evaluation.ragas_bridge.build_ragas_llm", return_value="LLM"),
    ):
        scores = _run_ragas_evaluate([{**SAMPLE, "reference": "RAG."}], _llm_config(), Settings())
    assert scores == {CONTEXT_RECALL: 1.0}
    kwargs = evaluate.call_args.kwargs
    assert "context_recall" in [m.name for m in kwargs["metrics"]]
    assert kwargs["dataset"].samples[0].reference == "RAG."


def test_run_ragas_evaluate_needs_credentials() -> None:
    with pytest.raises(EvaluationError, match="No LLM credentials"):
        _run_ragas_evaluate([SAMPLE], _llm_config(api_key=""), Settings())


def test_run_ragas_evaluate_fails_when_nothing_scored() -> None:
    evaluate = MagicMock(return_value=_fake_result([{"faithfulness": math.nan}]))
    emb = MagicMock()
    emb.__enter__.return_value = SimpleNamespace(ragas="EMB")
    with (
        patch("ragas.evaluate", evaluate),
        patch("callobuzz_rag.evaluation.ragas_bridge.RagasEmbeddings", return_value=emb),
        patch("callobuzz_rag.evaluation.ragas_bridge.build_ragas_llm", return_value="LLM"),
        pytest.raises(EvaluationError, match="Every RAGAS metric failed"),
    ):
        _run_ragas_evaluate([SAMPLE], _llm_config(base_url="http://ollama:11434"), Settings())


async def test_runner_attaches_golden_references_and_corpus_llm() -> None:
    corpus_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    log = MagicMock(
        query_text="  What   is RAG? ",
        response="An answer.",
        retrieved_chunk_ids=[chunk_id],
    )
    chunk = MagicMock(id=chunk_id, text="ctx")

    logs = MagicMock()
    logs.scalars.return_value.all.return_value = [log]
    chunks = MagicMock()
    chunks.scalars.return_value.all.return_value = [chunk]
    refs = MagicMock()
    refs.all.return_value = [("what is rag?", "Retrieval-augmented generation.")]

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[logs, chunks, refs, MagicMock()])
    session.get = AsyncMock(
        side_effect=lambda model, key: (
            SimpleNamespace(llm_config={"model": "anthropic/claude-x"}, tenant_id=uuid.uuid4())
            if key == corpus_id
            else None
        )
    )
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    runner = EvaluationRunner(session_factory=MagicMock(return_value=ctx), settings=Settings())
    with (
        patch(
            "callobuzz_rag.evaluation.runner._run_ragas_evaluate",
            return_value={FAITHFULNESS: 1.0},
        ) as run,
        # P5: the corpus's own model is honoured only where the workspace may use its own key
        patch("callobuzz_rag.billing.byok.byok_allowed", AsyncMock(return_value=True)),
    ):
        eval_run = await runner.run(corpus_id=corpus_id)

    assert eval_run.status == "completed", eval_run.error_message
    samples, llm_config, _settings, _per_sample = run.call_args.args
    assert samples[0]["reference"] == "Retrieval-augmented generation."
    assert llm_config.model == "anthropic/claude-x"
