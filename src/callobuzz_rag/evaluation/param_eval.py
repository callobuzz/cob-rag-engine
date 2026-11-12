"""Evaluating one retrieval parameter set against golden questions (the optimizer's trial).

`RetrievalOptimizer` proposes a set of retrieval parameters (`analytics/optimizer_types.py`
`SEARCH_SPACE`: engine setting names) and needs three numbers back for it: faithfulness and
answer relevancy (RAGAS, the evaluation add-on) and the 95th percentile question latency. A trial
runs each golden question through the real retrieval pipeline and the knowledge base's own answer
model with the parameters applied to a *copy* of the engine settings, then scores the answers.

Why not the golden-set runner (`golden_runner.py`)? That one answers through the assistant
director (labels, routes, confidence gate, translation) for a saved snapshot of the assistant's
settings, and stores an `EvalRun` with a row per question. A trial needs the retrieval pipeline
alone with settings the snapshot cannot express (`rrf_k`, the rerank funnel, the sparse width),
dozens of times per optimisation run, and must leave no run rows or query logs behind.

Trial answers are not metered as usage and not logged as query traffic: the optimizer is an
operator job that spends the knowledge base's own model key, and `QueryOrchestrator.query` (which
logs) is bypassed on purpose.
"""

import logging
import math
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import EvaluationError
from callobuzz_rag.evaluation.metrics import ANSWER_RELEVANCY, FAITHFULNESS

logger = logging.getLogger(__name__)

# The latency objective is a tail, not a mean: a config that is usually fast and sometimes
# very slow must not look good.
LATENCY_PERCENTILE = 95.0


@dataclass(frozen=True)
class TrialAnswer:
    """What the pipeline produced for one question."""

    answer: str
    contexts: list[str]


# What `open_trial` yields: `.judge_config` (the resolved LLM that judges) and
# `await .answer(pair) -> TrialAnswer`.
OpenTrial = Callable[[Settings, uuid.UUID, dict[str, Any]], AbstractAsyncContextManager[Any]]
# (samples, judge_config, settings) -> one {metric: score or None} dict per sample. Sync: the
# evaluator calls it with `asyncio.to_thread`.
Scorer = Callable[[list[Any], Any, Settings], list[dict[str, float | None]]]


def percentile(values: list[float], pct: float) -> float:
    """`pct` (0-100) of `values` with linear interpolation between ranks."""
    if not values:
        raise ValueError("percentile of nothing")
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100.0
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _ragas_scorer(
    samples: list[Any], judge_config: Any, settings: Settings
) -> list[dict[str, float | None]]:
    from callobuzz_rag.evaluation.scoring import score_samples

    return score_samples(samples, judge_config, settings, objectives_only=True)


def _mean(scores: list[dict[str, float | None]], metric: str) -> float | None:
    seen = [v for row in scores if (v := row.get(metric)) is not None]
    return sum(seen) / len(seen) if seen else None


class ParamEvaluator:
    """Scores a retrieval parameter set on a list of golden pairs.

    `open_trial` and `scorer` are the two seams (unit tests inject fakes); by default a trial
    opens the real pipeline for the pair's knowledge base and the scorer is RAGAS.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session_factory: Any = None,
        open_trial: OpenTrial | None = None,
        scorer: Scorer | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._open_trial = open_trial
        self._scorer = scorer
        self._clock = clock

    def _base_settings(self) -> Settings:
        if self._settings is not None:
            return self._settings
        from callobuzz_rag.core.config import get_settings

        return get_settings()

    async def evaluate(self, params: dict[str, Any], golden_pairs: list[Any]) -> dict[str, Any]:
        """Run the golden questions with `params` applied; return the optimizer's objectives.

        Returns `faithfulness` and `answer_relevancy` (means over the scored questions),
        `latency_p95` in seconds, `questions` (answered) and `failed_questions` (skipped).
        Raises `EvaluationAddonMissing` without the evaluation add-on, and `EvaluationError`
        when nothing could be answered or scored.
        """
        import asyncio

        if self._scorer is None:
            from callobuzz_rag.evaluation.optional import require_ragas

            require_ragas()  # before the questions are answered, not after they are paid for
        if not golden_pairs:
            raise EvaluationError("No golden question to evaluate")

        base = self._base_settings()
        unknown = sorted(k for k in params if k not in Settings.model_fields)
        if unknown:
            raise EvaluationError(f"Not engine settings: {', '.join(unknown)}")
        run_settings = base.model_copy(update=dict(params))

        by_corpus: dict[uuid.UUID, list[Any]] = defaultdict(list)
        for pair in golden_pairs:
            by_corpus[pair.corpus_id].append(pair)

        opener = self._open_trial or self._default_open_trial
        scorer = self._scorer or _ragas_scorer
        durations: list[float] = []
        scores: list[dict[str, float | None]] = []
        failed = 0
        for corpus_id, pairs in by_corpus.items():
            async with opener(run_settings, corpus_id, dict(params)) as trial:
                samples: list[Any] = []
                for pair in pairs:
                    started = self._clock()
                    try:
                        answered = await trial.answer(pair)
                    except Exception:
                        failed += 1
                        logger.warning(
                            "Trial question failed (corpus=%s)", corpus_id, exc_info=True
                        )
                        continue
                    durations.append(self._clock() - started)
                    samples.append(_sample(pair, answered))
                if samples:
                    scores.extend(
                        await asyncio.to_thread(scorer, samples, trial.judge_config, base)
                    )

        if not durations:
            raise EvaluationError(
                f"No golden question could be answered ({failed} failed); see the worker log"
            )
        faithfulness = _mean(scores, FAITHFULNESS)
        relevancy = _mean(scores, ANSWER_RELEVANCY)
        if faithfulness is None or relevancy is None:
            raise EvaluationError(
                f"The judge model scored none of the {len(durations)} answers "
                f"(faithfulness={faithfulness}, answer_relevancy={relevancy}); see the worker log"
            )
        return {
            FAITHFULNESS: faithfulness,
            ANSWER_RELEVANCY: relevancy,
            "latency_p95": percentile(durations, LATENCY_PERCENTILE),
            "questions": len(durations),
            "failed_questions": failed,
        }

    def _default_open_trial(
        self, run_settings: Settings, corpus_id: uuid.UUID, params: dict[str, Any]
    ) -> AbstractAsyncContextManager[Any]:
        if self._session_factory is None:
            raise EvaluationError("Evaluating parameters needs a database session factory")
        return open_corpus_trial(self._session_factory, run_settings, corpus_id, params)


def _sample(pair: Any, answered: TrialAnswer) -> Any:
    from callobuzz_rag.evaluation.scoring import ScoringSample

    return ScoringSample(pair.query, answered.answer, answered.contexts, pair.expected_answer)


class _CorpusTrial:
    """One knowledge base's pipeline for one trial."""

    def __init__(
        self,
        corpus_id: uuid.UUID,
        orchestrator: Any,
        llm: Any,
        judge_config: Any,
        retrieval: dict[str, Any],
        prompt: Any,
    ) -> None:
        self._corpus_id = corpus_id
        self._orchestrator = orchestrator
        self._llm = llm
        self.judge_config = judge_config
        self._retrieval = retrieval
        self._prompt = prompt

    async def answer(self, pair: Any) -> TrialAnswer:
        pipeline = await self._orchestrator.run_retrieval_pipeline(
            text=pair.query,
            corpus_id=self._corpus_id,
            **self._retrieval,
            system_prompt=self._prompt.system_prompt,
            constraints=self._prompt.constraints,
            response_language=self._prompt.response_language,
        )
        assembled = pipeline.assembled
        response = await self._llm.complete(assembled.messages)
        return TrialAnswer(
            answer=response.content, contexts=[chunk.text for chunk in assembled.cited_chunks]
        )


@asynccontextmanager
async def open_corpus_trial(
    session_factory: Any, run_settings: Settings, corpus_id: uuid.UUID, params: dict[str, Any]
) -> AsyncIterator[_CorpusTrial]:
    """The real pipeline for one knowledge base, with the trial's parameters applied.

    Mirrors `conversation.surfaces.director_for_worker` (a worker has no request): the
    corpus's own LLM, prompt, embedder and retrieval switches, never X-LLM-* headers. The
    parameters sit on top of whatever the corpus configured, so a corpus-level `rrf_k` or
    `mmr_lambda` does not hide the value being tried. A trial runs in the caller's event
    loop, so every client built here is closed before the context ends.

    No pipeline profile (PIP1) is laid over a trial on purpose: the optimizer measures the
    knowledge base's own configuration, and a profile would change what is being measured.
    """
    from callobuzz_rag.api.routes.query import _select_reranker
    from callobuzz_rag.conversation.surfaces import retrieval_kwargs
    from callobuzz_rag.core.config_resolver import ConfigResolver
    from callobuzz_rag.core.secrets import encryptor_from_settings
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.embeddings.factory import create_embedding_provider, embedder_for_corpus
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.retrieval.context_assembly import ContextAssembler
    from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
    from callobuzz_rag.retrieval.safety import ContentSanitizer
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

    async with session_factory() as session:
        corpus = await session.get(Corpus, corpus_id)
        if corpus is None:
            raise EvaluationError(f"Knowledge base {corpus_id} not found")
        resolver = ConfigResolver(run_settings, encryptor=encryptor_from_settings(run_settings))
        from callobuzz_rag.billing.byok import byok_allowed, usable_llm_config

        # a business without the own-key allowance is measured on the deployer's key (P5)
        resolved_llm = resolver.resolve_llm(
            request_headers=None,
            corpus_llm_config=usable_llm_config(
                corpus.llm_config, allowed=await byok_allowed(session, corpus.tenant_id)
            ),
        )
        resolved_prompt = resolver.resolve_prompt(
            request_body=None, corpus_prompt_config=corpus.prompt_config
        )
        resolved = resolver.resolve_retrieval(corpus_retrieval_config=corpus.retrieval_config)

        corpus_level = {
            "fusion_strategy": resolved.fusion_strategy,
            "hybrid_dense_weight": resolved.hybrid_dense_weight,
            "rrf_k": resolved.rrf_k,
            "mmr_lambda": resolved.mmr_lambda,
            "collapse_duplicates": resolved.collapse_duplicates,
        }
        # The trial's own values win over the corpus's.
        trial_settings = run_settings.model_copy(
            update={k: v for k, v in corpus_level.items() if k not in params}
        )
        retrieval = retrieval_kwargs(None, resolved)
        if "retrieval_top_k_dense" in params:
            retrieval["top_k"] = int(params["retrieval_top_k_dense"])

        llm = LiteLLMProvider.from_resolved_config(resolved_llm)
        default_embedder = create_embedding_provider(trial_settings, cache=False)
        embedder = embedder_for_corpus(corpus, trial_settings, default_embedder, cache=False)
        vector_store = QdrantVectorProvider(trial_settings)
        try:
            orchestrator = QueryOrchestrator(
                session=session,
                settings=trial_settings,
                embedder=embedder,
                vector_store=vector_store,
                llm_provider=llm,
                context_assembler=ContextAssembler(
                    trial_settings, sanitizer=ContentSanitizer(trial_settings)
                ),
                # As the golden runner: only the LLM reranker runs in a worker, so the rerank
                # funnel parameters matter for a corpus that chose it and are inert otherwise.
                reranker=_select_reranker(None, llm, resolved),
                collection_name=corpus.qdrant_collection,
            )
            yield _CorpusTrial(
                corpus_id, orchestrator, llm, resolved_llm, retrieval, resolved_prompt
            )
        finally:
            for resource in {
                id(embedder): embedder,
                id(default_embedder): default_embedder,
            }.values():
                await resource.close()
            await vector_store.close()
