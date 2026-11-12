"""RAGAS evaluation runner — automated RAG quality assessment.

Fetches recent query logs with their responses and retrieved chunks,
builds a RAGAS EvaluationDataset, runs configured metrics, and stores
results in the eval_run table.

Usage:
    runner = EvaluationRunner(session_factory=async_session)
    eval_run = await runner.run(sample_size=50, triggered_by="manual")
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.exceptions import EvaluationError
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.eval_run import EvalRun
from callobuzz_rag.db.models.query_log import QueryLog
from callobuzz_rag.evaluation.metrics import (
    ALL_METRICS,
    ANSWER_RELEVANCY,
    CONTEXT_PRECISION,
    CONTEXT_RECALL,
    DEFAULT_THRESHOLDS,
    FAITHFULNESS,
    MetricResult,
    MetricThresholds,
)

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.core.config_resolver import ResolvedLLMConfig

logger = logging.getLogger(__name__)


# RAGAS names a metric by its class; the engine stores the four it reports under
# these names (evaluation/metrics.py). Context precision is measured without a
# reference answer, so RAGAS calls it llm_context_precision_without_reference.
_RAGAS_METRIC_NAMES: dict[str, str] = {
    "faithfulness": FAITHFULNESS,
    "answer_relevancy": ANSWER_RELEVANCY,
    "llm_context_precision_without_reference": CONTEXT_PRECISION,
    "context_recall": CONTEXT_RECALL,
}


def _run_ragas_evaluate(
    samples_data: list[dict[str, Any]],
    llm_config: "ResolvedLLMConfig | None" = None,
    settings: "Settings | None" = None,
    per_sample: list[dict[str, float | None]] | None = None,
) -> dict[str, float]:
    """Run RAGAS evaluate() synchronously. Isolated for easy mocking.

    Deferred import: ragas is heavy and not needed at module load time.

    RAGAS judges with the engine's own LLM (`llm_config`: the corpus's resolved
    model and key, or the engine default) and embeds with the engine's
    embedding provider; without them it would demand an OPENAI_API_KEY.
    Context recall needs a reference answer, so it runs only when every sample
    has one (a golden pair for the same question).

    Args:
        samples_data: Dicts with user_input, response, retrieved_contexts and
            optionally reference.
        llm_config: The judge model. Engine default when None.
        settings: Engine settings. `get_settings()` when None.
        per_sample: When given, one `{engine metric: score or None}` dict per sample is
            appended to it (same order as `samples_data`), from the same RAGAS pass: the
            run's per-question rows cost no second evaluation.

    Returns:
        Dict mapping engine metric name -> aggregate score.
    """
    from callobuzz_rag.evaluation.optional import require_ragas

    require_ragas()

    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.metrics import (
        Faithfulness,
        LLMContextPrecisionWithoutReference,
        LLMContextRecall,
        ResponseRelevancy,
    )
    from ragas.run_config import RunConfig

    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.core.config_resolver import ConfigResolver
    from callobuzz_rag.core.secrets import encryptor_from_settings
    from callobuzz_rag.evaluation.ragas_bridge import (
        RagasEmbeddings,
        aggregate_scores,
        build_ragas_llm,
    )

    settings = settings or get_settings()
    if llm_config is None:
        resolver = ConfigResolver(settings, encryptor=encryptor_from_settings(settings))
        llm_config = resolver.resolve_llm()
    if not llm_config.api_key and not llm_config.base_url:
        raise EvaluationError(
            "No LLM credentials to judge with: set RAG_LLM_API_KEY, or give the corpus its "
            "own llm_config key"
        )

    with_reference = all(s.get("reference") for s in samples_data)
    ragas_samples = [
        SingleTurnSample(
            user_input=s["user_input"],
            response=s["response"],
            retrieved_contexts=s["retrieved_contexts"],
            reference=s.get("reference") if with_reference else None,
        )
        for s in samples_data
    ]
    metrics: list[Any] = [
        Faithfulness(),
        ResponseRelevancy(),
        LLMContextPrecisionWithoutReference(),
    ]
    if with_reference:
        metrics.append(LLMContextRecall())

    with RagasEmbeddings(settings) as embeddings:
        result = evaluate(
            dataset=EvaluationDataset(samples=ragas_samples),
            metrics=metrics,
            llm=build_ragas_llm(llm_config),
            embeddings=embeddings.ragas,
            run_config=RunConfig(max_workers=4),
            show_progress=False,
        )

    if per_sample is not None:
        for row in result.scores:
            per_sample.append(
                {
                    engine: _score_or_none(row.get(ragas))
                    for ragas, engine in _RAGAS_METRIC_NAMES.items()
                    if ragas in row
                }
            )
    scores = aggregate_scores(list(result.scores), _RAGAS_METRIC_NAMES)
    if not scores:
        raise EvaluationError(
            f"Every RAGAS metric failed on all {len(samples_data)} samples; the judge model "
            f"({llm_config.model}) returned nothing RAGAS could parse. See the worker log."
        )
    return scores


def _score_or_none(value: Any) -> float | None:
    """A RAGAS cell as a score; NaN (an unparseable judge reply) is no score."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value != value:
        return None
    return float(value)


def _normalize_question(text: str) -> str:
    """Question text compared case- and whitespace-insensitively."""
    return " ".join(text.split()).lower()


class _SampleData:
    """Lightweight container for a single evaluation sample."""

    __slots__ = ("query_log_id", "response", "retrieved_contexts", "user_input")

    def __init__(
        self,
        user_input: str,
        response: str,
        retrieved_contexts: list[str],
        query_log_id: uuid.UUID | None = None,
    ) -> None:
        self.user_input = user_input
        self.response = response
        self.retrieved_contexts = retrieved_contexts
        self.query_log_id = query_log_id


class EvaluationRunner:
    """Orchestrates RAGAS evaluation runs against recent query logs."""

    def __init__(
        self,
        session_factory: Any,
        thresholds: MetricThresholds | None = None,
        webhook_dispatcher: Any | None = None,
        score_drop_threshold: float = 0.10,
        settings: "Settings | None" = None,
        param_evaluator: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._thresholds = thresholds or DEFAULT_THRESHOLDS
        self._webhook_dispatcher = webhook_dispatcher
        self._score_drop_threshold = score_drop_threshold
        self._settings = settings
        self._param_evaluator = param_evaluator

    async def evaluate_with_params(
        self, params: dict[str, Any], golden_pairs: list[Any]
    ) -> dict[str, Any]:
        """Score one retrieval parameter set on golden pairs (what the optimizer calls per trial).

        Answers each question through the retrieval pipeline with `params` applied to a copy of
        the engine settings and returns `faithfulness`, `answer_relevancy` and `latency_p95`
        (see `evaluation/param_eval.py`). Needs the evaluation add-on.
        """
        if self._param_evaluator is None:
            from callobuzz_rag.evaluation.param_eval import ParamEvaluator

            self._param_evaluator = ParamEvaluator(
                self._settings, session_factory=self._session_factory
            )
        return await self._param_evaluator.evaluate(params, golden_pairs)

    async def run(
        self,
        sample_size: int = 50,
        triggered_by: str = "manual",
        corpus_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> EvalRun:
        """Execute a full evaluation run.

        Steps:
        1. Create EvalRun record (status=running)
        2. Fetch recent query_log entries with responses
        3. Fetch corresponding chunk texts
        4. Build RAGAS dataset
        5. Run ragas.evaluate() via asyncio.to_thread()
        6. Parse results, store in eval_run record
        7. Update status to completed (or failed on error)

        Args:
            sample_size: Max number of query logs to evaluate.
            triggered_by: Trigger source — "manual", "schedule", or "config_change".
            corpus_id: Optional corpus to scope evaluation to. If None, all corpora.
            run_id: A row the API already created (status "queued") and handed
                back to the caller to poll. It is picked up and moved to
                "running"; without it a new row is created (scheduled runs).

        Returns:
            The completed EvalRun record.
        """
        async with self._session_factory() as session:
            eval_run = await session.get(EvalRun, run_id) if run_id is not None else None
            if eval_run is None:
                if run_id is not None:
                    logger.warning("Eval run %s not found; recording a new run", run_id)
                eval_run = EvalRun()
                eval_run.triggered_by = triggered_by
                eval_run.corpus_id = corpus_id
                session.add(eval_run)
            eval_run.status = "running"
            eval_run.sample_size = 0
            await session.commit()

            try:
                # Fetch recent query logs with responses
                if corpus_id is not None:
                    logger.info("Evaluation scoped to corpus_id=%s", corpus_id)
                query_logs = await self._fetch_query_logs(session, sample_size, corpus_id=corpus_id)

                if not query_logs:
                    logger.info("No query logs found for evaluation")
                    eval_run.status = "completed"
                    eval_run.sample_size = 0
                    eval_run.metrics = {}
                    eval_run.completed_at = datetime.now(UTC)
                    await session.commit()
                    return eval_run

                # Fetch chunk texts for all referenced chunk IDs
                all_chunk_ids = self._collect_chunk_ids(query_logs)
                chunks_by_id = await self._fetch_chunks(session, all_chunk_ids)

                # Build samples, filtering out incomplete data
                samples = self._build_samples(query_logs, chunks_by_id)

                if not samples:
                    logger.warning("No valid samples after filtering — skipping RAGAS")
                    eval_run.status = "completed"
                    eval_run.sample_size = 0
                    eval_run.metrics = {}
                    eval_run.completed_at = datetime.now(UTC)
                    await session.commit()
                    return eval_run

                # Golden pairs asked the same question supply reference answers
                # (context recall needs one); the judge is the corpus's own LLM.
                references = await self._fetch_references(
                    session, [s.user_input for s in samples], corpus_id
                )
                llm_config = await self._resolve_llm_config(session, corpus_id)

                # Run RAGAS evaluate in thread (blocking call)
                samples_data = [
                    {
                        "user_input": s.user_input,
                        "response": s.response,
                        "retrieved_contexts": s.retrieved_contexts,
                        "reference": references.get(_normalize_question(s.user_input)),
                    }
                    for s in samples
                ]

                per_sample: list[dict[str, float | None]] = []
                raw_scores = await asyncio.to_thread(
                    _run_ragas_evaluate, samples_data, llm_config, self._settings, per_sample
                )

                # Parse into MetricResult objects
                metric_results = self._parse_results(raw_scores)

                # Store results
                eval_run.status = "completed"
                eval_run.sample_size = len(samples)
                eval_run.metrics = {r.name: r.to_dict() for r in metric_results}
                eval_run.completed_at = datetime.now(UTC)
                self._add_samples(session, eval_run, samples, samples_data, per_sample)
                await session.commit()

                # Fire webhook on significant score drops vs previous run
                previous_scores = await self._fetch_previous_scores(
                    session, eval_run.id, eval_run.corpus_id
                )
                await self._check_and_fire_score_drop(str(eval_run.id), raw_scores, previous_scores)

                logger.info(
                    "Evaluation complete: %d samples, %d metrics",
                    len(samples),
                    len(metric_results),
                )

            except Exception as exc:
                logger.error("Evaluation failed: %s", exc, exc_info=True)
                eval_run.status = "failed"
                eval_run.error_message = str(exc)
                eval_run.completed_at = datetime.now(UTC)
                await session.commit()

        return eval_run

    def _add_samples(
        self,
        session: AsyncSession,
        eval_run: EvalRun,
        samples: list[_SampleData],
        samples_data: list[dict[str, Any]],
        per_sample: list[dict[str, float | None]],
    ) -> None:
        """One `EvalRunSample` per evaluated query log, so a traffic run is browsable
        question by question like a golden run (conversation C7)."""
        from callobuzz_rag.db.models.eval_run_sample import EvalRunSample

        if len(per_sample) != len(samples):
            return  # the scorer reported no per-question rows
        for position, (sample, data, scores) in enumerate(
            zip(samples, samples_data, per_sample, strict=True)
        ):
            scored = {k: v for k, v in scores.items() if v is not None}
            session.add(
                EvalRunSample(
                    run_id=eval_run.id,
                    query_log_id=sample.query_log_id,
                    question=sample.user_input,
                    expected_answer=data.get("reference"),
                    answer=sample.response,
                    contexts=[{"text": c[:1000]} for c in sample.retrieved_contexts],
                    scores=scores,
                    reasons={},
                    tags={},
                    passed=(
                        all(v >= self._thresholds.get(k) for k, v in scored.items())
                        if scored
                        else None
                    ),
                    position=position,
                )
            )

    async def _fetch_query_logs(
        self,
        session: AsyncSession,
        limit: int,
        corpus_id: uuid.UUID | None = None,
    ) -> list[QueryLog]:
        """Fetch most recent query logs that have responses.

        Args:
            session: Async DB session.
            limit: Maximum number of logs to fetch.
            corpus_id: Optional corpus filter. If None, fetches across all corpora.
        """
        stmt = (
            select(QueryLog)
            .where(QueryLog.response.is_not(None))
            .where(QueryLog.query_text.is_not(None))
        )
        if corpus_id is not None:
            stmt = stmt.where(QueryLog.corpus_id == corpus_id)
        stmt = stmt.order_by(QueryLog.created_at.desc()).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _fetch_references(
        self,
        session: AsyncSession,
        questions: list[str],
        corpus_id: uuid.UUID | None,
    ) -> dict[str, str]:
        """Expected answers of golden pairs that ask one of `questions`.

        Matched on the whitespace- and case-normalized question text, within
        the run's corpus when it has one. Keyed by the normalized question.
        """
        from callobuzz_rag.db.models.golden_pair import GoldenPair

        wanted = {_normalize_question(q) for q in questions}
        if not wanted:
            return {}
        stmt = select(GoldenPair.query, GoldenPair.expected_answer).where(
            func.lower(func.btrim(func.regexp_replace(GoldenPair.query, r"\s+", " ", "g"))).in_(
                wanted
            )
        )
        if corpus_id is not None:
            stmt = stmt.where(GoldenPair.corpus_id == corpus_id)
        result = await session.execute(stmt)
        references: dict[str, str] = {}
        for query, expected in result.all():
            key = _normalize_question(query)
            if key in wanted and expected:
                references.setdefault(key, expected)
        return references

    async def _resolve_llm_config(
        self, session: AsyncSession, corpus_id: uuid.UUID | None
    ) -> "ResolvedLLMConfig":
        """The judge model: the corpus's LLM config, else the engine default."""
        from callobuzz_rag.core.config import get_settings
        from callobuzz_rag.core.config_resolver import ConfigResolver
        from callobuzz_rag.core.secrets import encryptor_from_settings
        from callobuzz_rag.db.models.corpus import Corpus

        settings = self._settings or get_settings()
        from callobuzz_rag.billing.byok import byok_allowed, usable_llm_config

        corpus_llm_config = None
        if corpus_id is not None:
            corpus = await session.get(Corpus, corpus_id)
            if corpus is not None:
                # no own-key allowance (P5): the judge runs on the deployer's key
                corpus_llm_config = usable_llm_config(
                    corpus.llm_config, allowed=await byok_allowed(session, corpus.tenant_id)
                )
        resolver = ConfigResolver(settings, encryptor=encryptor_from_settings(settings))
        return resolver.resolve_llm(request_headers=None, corpus_llm_config=corpus_llm_config)

    def _collect_chunk_ids(
        self,
        query_logs: list[QueryLog],
    ) -> list[uuid.UUID]:
        """Collect all unique chunk IDs referenced by query logs."""
        chunk_ids: set[uuid.UUID] = set()
        for log in query_logs:
            if log.retrieved_chunk_ids:
                chunk_ids.update(log.retrieved_chunk_ids)
        return list(chunk_ids)

    async def _fetch_chunks(
        self, session: AsyncSession, chunk_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Chunk]:
        """Fetch chunks by ID and return as lookup dict."""
        if not chunk_ids:
            return {}

        stmt = select(Chunk).where(Chunk.id.in_(chunk_ids))
        result = await session.execute(stmt)
        chunks = result.scalars().all()
        return {chunk.id: chunk for chunk in chunks}

    def _build_samples(
        self,
        query_logs: list[Any],
        chunks_by_id: dict[uuid.UUID, Any],
    ) -> list[_SampleData]:
        """Build evaluation samples from query logs and their chunks.

        Skips logs that are missing query_text, response, or have no
        retrievable chunks.
        """
        samples: list[_SampleData] = []

        for log in query_logs:
            if not log.query_text or not log.response:
                continue

            chunk_ids = log.retrieved_chunk_ids or []
            if not chunk_ids:
                continue

            # Resolve chunk texts
            contexts: list[str] = []
            for cid in chunk_ids:
                chunk = chunks_by_id.get(cid)
                if chunk and chunk.text:
                    contexts.append(chunk.text)

            if not contexts:
                continue

            samples.append(
                _SampleData(
                    user_input=log.query_text,
                    response=log.response,
                    retrieved_contexts=contexts,
                    query_log_id=getattr(log, "id", None),
                )
            )

        return samples

    async def _fetch_previous_scores(
        self,
        session: AsyncSession,
        current_run_id: uuid.UUID,
        corpus_id: uuid.UUID | None = None,
    ) -> dict[str, float]:
        """Fetch aggregate scores from the most recent completed traffic run of the same
        scope (one corpus, or all of them) before this one. A golden run, or another
        corpus's, is not a baseline for these scores."""
        try:
            stmt = (
                select(EvalRun)
                .where(
                    EvalRun.status == "completed",
                    EvalRun.id != current_run_id,
                    EvalRun.kind == "traffic",
                    EvalRun.corpus_id.is_(None)
                    if corpus_id is None
                    else EvalRun.corpus_id == corpus_id,
                )
                .order_by(EvalRun.completed_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            prev_run = result.scalar_one_or_none()
            if prev_run is None or not prev_run.metrics:
                return {}
            # Extract aggregate score from each metric's stored dict
            return {
                name: data.get("score", 0.0) if isinstance(data, dict) else float(data)
                for name, data in prev_run.metrics.items()
            }
        except Exception:
            logger.warning("Failed to fetch previous eval scores", exc_info=True)
            return {}

    async def _check_and_fire_score_drop(
        self,
        eval_run_id: str,
        current_scores: dict[str, float],
        previous_scores: dict[str, float],
    ) -> None:
        """Compare current vs previous scores; fire webhook on significant drop."""
        if self._webhook_dispatcher is None:
            return

        for metric, new_score in current_scores.items():
            old_score = previous_scores.get(metric)
            if old_score is None:
                continue
            drop = old_score - new_score
            if drop >= self._score_drop_threshold:
                try:
                    from callobuzz_rag.core.events import WebhookEvent

                    await self._webhook_dispatcher.dispatch(
                        WebhookEvent.EVAL_SCORE_DROP,
                        {
                            "metric": metric,
                            "old_score": old_score,
                            "new_score": new_score,
                            "eval_run_id": eval_run_id,
                            "drop": round(drop, 4),
                        },
                    )
                except Exception:
                    logger.warning(
                        "Webhook dispatch failed for EVAL_SCORE_DROP (eval_run=%s)",
                        eval_run_id,
                        exc_info=True,
                    )
                return  # Fire once for the worst drop

    def _parse_results(
        self,
        raw_scores: dict[str, float],
    ) -> list[MetricResult]:
        """Convert raw RAGAS scores dict into MetricResult objects."""
        results: list[MetricResult] = []
        for metric_name, score in raw_scores.items():
            if metric_name in ALL_METRICS:
                threshold = self._thresholds.get(metric_name)
                results.append(
                    MetricResult(
                        name=metric_name,
                        score=score,
                        threshold=threshold,
                    )
                )
        return results
