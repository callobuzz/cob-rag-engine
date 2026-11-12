"""Running a golden set fresh: fast (behaviour) or full (answer + scores + criteria) (C7)."""

import asyncio
import inspect
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.analytics.usage import SOURCE_EVAL, payer_of, record_usage
from callobuzz_rag.conversation.testing import director_port
from callobuzz_rag.conversation.testing.asserts import CaseOutcome, CaseSpec, evaluate_case
from callobuzz_rag.db.models import Corpus, EvalRun, EvalRunSample, GoldenPair, GoldenSet
from callobuzz_rag.evaluation.golden import pair_from_fields
from callobuzz_rag.evaluation.judge import Criterion, explain_failure, judge_criteria
from callobuzz_rag.evaluation.metrics import CRITERION_PREFIX, DEFAULT_THRESHOLDS
from callobuzz_rag.evaluation.optional import ragas_available
from callobuzz_rag.evaluation.scoring import ScoringSample, score_samples
from callobuzz_rag.llm.cost import CostLedger, request_cost_of

logger = logging.getLogger(__name__)
STALE_AFTER = timedelta(minutes=60)
# Questions answered, then scored and saved together: a crash keeps every finished batch.
SCORE_BATCH = 5


def stale_cutoff() -> datetime:
    return datetime.now(UTC) - STALE_AFTER


async def _config_for(
    session: AsyncSession, corpus_id: uuid.UUID, assistant: Any, settings: Any
) -> Any | None:
    """The snapshot's configuration as C2's `RuntimeConfig` (README P2/P3), or None."""
    from callobuzz_rag.conversation.store import (
        AssistantConfigStore,
        RuntimeConfig,
        get_runtime_config,
        row_config,
    )

    if isinstance(assistant, int) and not isinstance(assistant, bool):
        row = await AssistantConfigStore(session, None, settings).get_version(corpus_id, assistant)
        config = row_config(row)
        return RuntimeConfig(
            config=config, version=row.version, draft_revision=None, enabled=config.enabled
        )
    return await get_runtime_config(session, corpus_id, use_draft=assistant == "draft")


async def judge_config_for(session: AsyncSession, corpus: Corpus, settings: Any) -> Any:
    """The judge: eval_settings' model/key when set, else the knowledge base's own LLM."""
    from callobuzz_rag.core.config_resolver import ConfigResolver
    from callobuzz_rag.core.secrets import encryptor_from_settings
    from callobuzz_rag.db.models import EvalSettings

    resolver = ConfigResolver(settings, encryptor=encryptor_from_settings(settings))
    base = _resolve_judge(resolver, corpus, settings, None)
    row = await session.get(EvalSettings, corpus.id)
    if row is None or not row.judge_model:
        return base
    headers = {
        "x-llm-provider": row.judge_provider or base.provider or "",
        "x-llm-model": row.judge_model,
    }
    if row.judge_api_key_encrypted:
        encryptor = encryptor_from_settings(settings)
        if encryptor is not None:
            headers["x-llm-api-key"] = encryptor.decrypt(row.judge_api_key_encrypted)
    # A custom host only ever gets the judge's own key, never the corpus or engine key.
    if row.judge_base_url and headers.get("x-llm-api-key"):
        headers["x-llm-base-url"] = row.judge_base_url
    return _resolve_judge(resolver, corpus, settings, {k: v for k, v in headers.items() if v})


def _resolve_judge(
    resolver: Any, corpus: Corpus, settings: Any, headers: dict[str, str] | None
) -> Any:
    """The judge's LLM config. The judge is the deployer's measuring tool, not an answer to a
    visitor, so no pipeline profile is laid over it (a Free business's runs are still judged by
    a real model); it still goes through `resolve_with_profile` like every other resolution."""
    from callobuzz_rag.tiers.profiles import noop_profile
    from callobuzz_rag.tiers.resolve import resolve_with_profile

    llm, _ = resolve_with_profile(
        resolver, corpus, headers=headers, profile=noop_profile(), settings=settings
    )
    return llm


def _judge_provider(config: Any) -> Any:
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

    return LiteLLMProvider.from_resolved_config(config)


def _spec(pair: GoldenPair) -> CaseSpec | None:
    if not pair.expected_behaviour:
        return None
    return CaseSpec(
        pair.expected_behaviour,  # type: ignore[arg-type]
        pair.expected_label,
        tuple(pair.must_include or ()),
        pair.must_cite,
    )


def _tags(pair: GoldenPair) -> dict[str, Any]:
    return {"origin": pair.origin, "difficulty": pair.difficulty, "topic": pair.topic}


def _behaviour(pair: GoldenPair, outcome: CaseOutcome, verdict: Any) -> dict[str, Any] | None:
    if verdict is None:
        return None
    return {
        "expected": pair.expected_behaviour,
        "label": outcome.label,
        "route": outcome.route,
        "gate_passed": outcome.gate_passed,
        "checks": [c.as_dict() for c in verdict.checks],
    }


ADMIN_INITIATED = "admin_initiated"  # in `EvalRun.config_snapshot`: the admin principal asked


def public_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    """The run's settings as the API shows them (without the billing marker)."""
    if snapshot is None:
        return None
    return {k: v for k, v in snapshot.items() if k != ADMIN_INITIATED}


async def _close(director: Any) -> None:
    close = getattr(director, "aclose", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def _claim(session: AsyncSession, run_id: uuid.UUID) -> bool:
    """Take a queued (or stale running) run for this worker, atomically: with late
    acknowledgement a killed task is delivered again and must not run beside itself."""
    result = await session.execute(
        update(EvalRun)
        .where(
            EvalRun.id == run_id,
            or_(
                EvalRun.status == "queued",
                and_(EvalRun.status == "running", EvalRun.started_at < stale_cutoff()),
            ),
        )
        .values(status="running", started_at=datetime.now(UTC))
        .returning(EvalRun.id)
    )
    return result.scalar_one_or_none() is not None


def _metrics_of(
    rows: list[EvalRunSample], run: EvalRun, iterations: int, min_samples: int
) -> dict[str, Any]:
    """The run's aggregate in the shape every reader of `eval_run.metrics` already knows
    ({metric: {name, score, threshold, passed}}), plus n and the interval."""
    from callobuzz_rag.evaluation.compare import metric_summary

    out: dict[str, Any] = {}
    for s in metric_summary(
        rows, run.thresholds or {}, iterations=iterations, min_samples=min_samples
    ):
        if s.mean is None:
            continue
        out[s.metric] = {
            "name": s.metric,
            "score": s.mean,
            "threshold": s.threshold,
            "passed": s.threshold is None or s.mean >= s.threshold,
            "n": s.n,
            "ci_low": s.ci_low,
            "ci_high": s.ci_high,
        }
    return out


class GoldenRunner:
    def __init__(self, session_factory: Any, settings: Any) -> None:
        self._factory = session_factory
        self._settings = settings

    async def execute(self, run_id: uuid.UUID) -> None:
        from callobuzz_rag.api.schemas.evals import ExperimentSettings
        from callobuzz_rag.billing.context import ADMIN_ANSWER

        async with self._factory() as session:
            if not await _claim(session, run_id):
                return
            await session.execute(delete(EvalRunSample).where(EvalRunSample.run_id == run_id))
            await session.commit()
            run = await session.get(EvalRun, run_id, populate_existing=True)
            if run is None:
                return
            director: Any = None
            try:
                raw_snapshot = dict(run.config_snapshot or {})
                # A run the operator started is never billed to the customer: the director's
                # answers and the judge meter write waived rows (the runner's task context).
                ADMIN_ANSWER.set(bool(raw_snapshot.pop(ADMIN_INITIATED, False)))
                snapshot = ExperimentSettings.model_validate(raw_snapshot)
                runtime = await _config_for(
                    session, run.corpus_id, snapshot.assistant, self._settings
                )
                if runtime is None:
                    raise RuntimeError("no assistant configuration for this knowledge base")
                run.config_version = runtime.version
                corpus = await session.get(Corpus, run.corpus_id)
                pairs = list(
                    (
                        await session.execute(
                            select(GoldenPair)
                            .where(GoldenPair.set_id == run.set_id)
                            .order_by(GoldenPair.position, GoldenPair.created_at)
                        )
                    )
                    .scalars()
                    .all()
                )
                if run.mode == "fast":
                    pairs = [p for p in pairs if p.expected_behaviour]
                # PIP1 profile: a run answers with the plan's profile, never a downgrade (D10)
                from callobuzz_rag.llm.local import ensure_status
                from callobuzz_rag.tiers import profiles as tier_profiles

                await ensure_status(self._settings)  # a worker has no readiness refresher
                profile = await tier_profiles.plan_profile(
                    session, corpus.tenant_id, settings=self._settings
                )
                if profile.config.answer_route == "local" and not profile.available:
                    raise RuntimeError("The free local model is not ready, so this run cannot start.")
                director = director_port.make_director(
                    self._settings, self._factory, corpus, runtime, snapshot, profile=profile
                )
                # /PIP1 profile
                if run.mode == "fast":
                    rows = await self._fast(session, run, pairs, director, corpus, runtime)
                else:
                    rows = await self._full(session, run, pairs, director, corpus, runtime)
                run.sample_size = len(rows)
                run.totals = {
                    "questions": len(rows),
                    "passed": sum(1 for r in rows if r.passed),
                    "failed": sum(1 for r in rows if r.passed is False),
                    "errors": sum(1 for r in rows if r.error),
                }
                run.metrics = _metrics_of(
                    rows,
                    run,
                    self._settings.eval_bootstrap_iterations,
                    self._settings.eval_min_samples,
                )
                run.status, run.completed_at = "completed", datetime.now(UTC)
            except Exception as exc:
                logger.error("Golden run %s failed: %s", run_id, exc, exc_info=True)
                await session.rollback()
                run.status = "failed"
                run.error_message = str(exc)[:2000]
                run.completed_at = datetime.now(UTC)
            finally:
                if director is not None:
                    await _close(director)
            await session.commit()

    async def _fast(
        self,
        session: Any,
        run: EvalRun,
        pairs: list[GoldenPair],
        director: Any,
        corpus: Any,
        runtime: Any,
    ) -> list[EvalRunSample]:
        # Fast runs plan each turn (label, route, gate): no answer, no judge. The plan step
        # still calls the fast model for the label, so its cost is the ledger delta around it;
        # with no ledger the cost is unknown, never a silent zero.
        rows: list[EvalRunSample] = []
        ledger = director_port._ledger(director)
        total_usd, known = 0.0, ledger is not None
        for position, pair in enumerate(pairs):
            started = time.monotonic()
            error = None
            mark = len(ledger.calls) if ledger is not None else 0
            try:
                outcome = await director_port.plan_pair(director, pair, corpus, runtime)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:2000]
                outcome = CaseOutcome(
                    "fast", None, None, None, None, None, (), True, True, error=error
                )
            spec = _spec(pair)
            verdict = evaluate_case(spec, outcome) if spec else None
            row_cost: float | None = None
            if ledger is not None:
                spent = CostLedger(calls=ledger.calls[mark:]).summary()
                row_cost = spent.cost_micro / 1_000_000
                total_usd += row_cost
                known = known and spent.cost_known
            row = EvalRunSample(
                run_id=run.id,
                golden_pair_id=pair.id,
                question=pair.query,
                expected_answer=pair.expected_answer,
                scores={},
                reasons={},
                contexts=[],
                tags=_tags(pair),
                error=error,
                position=position,
                behaviour=_behaviour(pair, outcome, verdict),
                passed=False if error else (None if verdict is None else verdict.passed),
                latency_ms=int((time.monotonic() - started) * 1000),
                cost_usd=row_cost,
            )
            session.add(row)
            rows.append(row)
            run.sample_size = len(rows)
            run.started_at = datetime.now(UTC)  # heartbeat: staleness is time since progress
            await session.commit()
        run.actual_cost_usd = total_usd if ledger is not None else None
        run.actual_cost_known = known
        return rows

    async def _full(
        self,
        session: Any,
        run: EvalRun,
        pairs: list[GoldenPair],
        director: Any,
        corpus: Any,
        runtime: Any,
    ) -> list[EvalRunSample]:
        judge_cfg = await judge_config_for(session, corpus, self._settings)
        run.judge_model = getattr(judge_cfg, "model", None)
        judge = _judge_provider(judge_cfg)
        thresholds = DEFAULT_THRESHOLDS.with_overrides(run.thresholds or {})
        criteria = [
            Criterion(c["name"], c["definition"], float(c["threshold"])) for c in run.criteria or []
        ]
        judge_ledger = getattr(judge, "cost_ledger", None)
        # Without the evaluation add-on the RAGAS metrics are not scored; the criteria, the
        # failure reasons and the behaviour checks (all through our own judge) still are.
        use_ragas = ragas_available()
        answers_usd, answers_known = 0.0, True
        rows: list[EvalRunSample] = []
        for start in range(0, len(pairs), SCORE_BATCH):
            batch = pairs[start : start + SCORE_BATCH]
            answered: list[tuple[GoldenPair, CaseOutcome, list[str], Any, int, str | None]] = []
            for pair in batch:
                began = time.monotonic()
                try:
                    outcome, contexts, usage = await director_port.answer_pair(
                        director, pair, corpus, runtime
                    )
                    answered.append(
                        (
                            pair,
                            outcome,
                            contexts,
                            usage,
                            int((time.monotonic() - began) * 1000),
                            None,
                        )
                    )
                except Exception as exc:
                    failure = f"{type(exc).__name__}: {exc}"[:2000]
                    failed = CaseOutcome(
                        "full", None, None, None, None, None, (), True, True, error=failure
                    )
                    answered.append((pair, failed, [], None, 0, failure))
            mark = len(judge_ledger.calls) if isinstance(judge_ledger, CostLedger) else 0
            # Only answers that retrieved documents have anything for the judge metrics.
            scorable = [a for a in answered if a[5] is None and a[1].answer_text and a[2]]
            # An answer that retrieved nothing still has a reference to be correct against.
            reference_only = [
                a
                for a in answered
                if a[5] is None and a[1].answer_text and not a[2] and a[0].expected_answer
            ]
            by_pair: dict[uuid.UUID, dict[str, float | None]] = {}
            scoring_error: str | None = None
            for group, only_answer in ((scorable, False), (reference_only, True)):
                if not group or not use_ragas:
                    continue
                samples = [
                    ScoringSample(p.query, o.answer_text or "", ctx, p.expected_answer)
                    for p, o, ctx, *_ in group
                ]
                try:
                    scores = await asyncio.to_thread(
                        score_samples,
                        samples,
                        judge_cfg,
                        self._settings,
                        provider=judge,
                        **({"answer_only": True} if only_answer else {}),
                    )
                except Exception as exc:
                    logger.warning("Scoring a batch of run %s failed", run.id, exc_info=True)
                    scoring_error = f"Scoring failed: {type(exc).__name__}: {exc}"[:2000]
                    scores = [{} for _ in group]
                by_pair.update({a[0].id: sc for a, sc in zip(group, scores, strict=True)})
            batch_rows: list[EvalRunSample] = []
            for position, (pair, outcome, contexts, usage, latency, error) in enumerate(
                answered, start=start
            ):
                sample_scores: dict[str, float | None] = dict(by_pair.get(pair.id, {}))
                reasons: dict[str, str] = {}
                if error is None and outcome.answer_text:
                    judged = await judge_criteria(
                        judge, pair.query, outcome.answer_text, contexts, criteria
                    )
                    for name, (score, reason) in judged.items():
                        sample_scores[CRITERION_PREFIX + name] = score
                        reasons[CRITERION_PREFIX + name] = reason
                failing = {
                    k: v
                    for k, v in sample_scores.items()
                    if v is not None
                    and not k.startswith(CRITERION_PREFIX)
                    and v < thresholds.get(k)
                }
                failing |= {
                    CRITERION_PREFIX + c.name: v
                    for c in criteria
                    if (v := sample_scores.get(CRITERION_PREFIX + c.name)) is not None
                    and v < c.threshold
                }
                if failing and outcome.answer_text:
                    reasons["summary"] = await explain_failure(
                        judge, pair.query, outcome.answer_text, pair.expected_answer, failing
                    )
                row_error = error
                if row_error is None and scoring_error and pair.id in by_pair:
                    row_error = scoring_error
                spec = _spec(pair)
                verdict = evaluate_case(spec, outcome) if spec else None
                passed = row_error is None and not failing and (verdict is None or verdict.passed)
                if (
                    use_ragas
                    and pair.expected_answer
                    and row_error is None
                    and not any(
                        v is not None
                        for k, v in sample_scores.items()
                        if not k.startswith(CRITERION_PREFIX)
                    )
                ):
                    # A reference answer that nothing scored (no answer from the documents)
                    # never counts as a pass.
                    passed = False
                    reasons["summary"] = "No answer from the documents"
                answer_usd = float(usage.cost_usd or 0.0) if usage is not None else 0.0
                answers_usd += answer_usd
                if usage is not None and not usage.cost_known:
                    answers_known = False
                batch_rows.append(
                    EvalRunSample(
                        run_id=run.id,
                        golden_pair_id=pair.id,
                        question=pair.query,
                        expected_answer=pair.expected_answer,
                        answer=outcome.answer_text,
                        contexts=[{"text": c[:1000]} for c in contexts],
                        scores=sample_scores,
                        reasons=reasons,
                        tags=_tags(pair),
                        passed=passed,
                        error=row_error,
                        behaviour=_behaviour(pair, outcome, verdict),
                        latency_ms=latency,
                        cost_usd=answer_usd,
                        position=position,
                    )
                )
            if isinstance(judge_ledger, CostLedger):
                batch_judge = CostLedger(calls=judge_ledger.calls[mark:]).summary()
                share = (batch_judge.cost_micro / 1_000_000) / len(batch_rows)
                for row in batch_rows:
                    row.cost_usd = (row.cost_usd or 0.0) + share
            for row in batch_rows:
                session.add(row)
            rows.extend(batch_rows)
            run.sample_size = len(rows)
            run.started_at = datetime.now(UTC)  # heartbeat: staleness is time since progress
            await session.commit()
        spent = request_cost_of(judge)
        judge_usd = spent.cost_micro / 1_000_000 if spent is not None else 0.0
        run.actual_cost_usd = answers_usd + judge_usd
        run.actual_cost_known = answers_known and (spent is None or spent.cost_known)
        if spent is not None:
            # The director meters its own answers; the runner meters what it called itself.
            try:
                await record_usage(
                    session,
                    corpus_id=corpus.id,
                    tenant_id=corpus.tenant_id,
                    source=SOURCE_EVAL,
                    prompt_tokens=spent.prompt_tokens,
                    completion_tokens=spent.completion_tokens,
                    model=run.judge_model,
                    requests=0,
                    cost=spent,
                    payer=payer_of(judge_cfg),
                    commit=False,
                    request_id=uuid.uuid5(run.id, "judge"),  # BIL3 request id
                )
            except Exception:
                logger.warning("Could not meter the judge calls of run %s", run.id, exc_info=True)
        await session.commit()
        return rows


def _dispatch(run_id: uuid.UUID) -> None:
    from callobuzz_rag.worker.celery_app import celery_app

    celery_app.send_task(
        "callobuzz_rag.run_evaluation",
        kwargs={"kind": "golden", "run_id": str(run_id)},
        queue="periodic",
    )


async def enqueue_publish_runs(
    session: AsyncSession, corpus_id: uuid.UUID, version: int, *, admin_initiated: bool = False
) -> int:
    """One fast run per set that has behaviour questions, after a publish. Best-effort."""
    set_ids = (
        (
            await session.execute(
                select(GoldenSet.id).where(
                    GoldenSet.corpus_id == corpus_id,
                    ~select(EvalRun.id)
                    .where(
                        EvalRun.set_id == GoldenSet.id,
                        EvalRun.status.in_(("queued", "running")),
                        EvalRun.started_at > stale_cutoff(),
                    )
                    .exists(),
                    select(GoldenPair.id)
                    .where(
                        GoldenPair.set_id == GoldenSet.id,
                        GoldenPair.expected_behaviour.is_not(None),
                    )
                    .exists(),
                )
            )
        )
        .scalars()
        .all()
    )
    runs = [
        EvalRun(
            id=uuid.uuid4(),
            corpus_id=corpus_id,
            kind="golden",
            set_id=set_id,
            mode="fast",
            target="published",
            config_version=version,
            triggered_by="publish",
            status="queued",
            sample_size=0,
            config_snapshot={"assistant": version}
            | ({ADMIN_INITIATED: True} if admin_initiated else {}),
            thresholds={},
            criteria=[],
            created_by="publish",
        )
        for set_id in set_ids
    ]
    if not runs:
        return 0
    for run in runs:
        session.add(run)
    await session.commit()
    for run in runs:
        try:
            _dispatch(run.id)
        except Exception:
            logger.warning("Publish test run %s not queued", run.id, exc_info=True)
    return len(runs)


async def create_golden_set(
    session: AsyncSession,
    corpus_id: uuid.UUID,
    *,
    name: str,
    source: str,
    pairs: list[Any],
) -> GoldenSet:
    """A new set with `pairs` (`api.schemas.evals.GoldenPairFields`); commits. For guided setup."""
    row = GoldenSet(
        id=uuid.uuid4(), corpus_id=corpus_id, name=name, source=source, is_default=False
    )
    session.add(row)
    await session.flush()  # pairs reference the set and no relationship orders the inserts
    for position, p in enumerate(pairs):
        session.add(pair_from_fields(corpus_id, row.id, p, position))
    await session.commit()
    return row


async def start_golden_run(
    session: AsyncSession,
    corpus_id: uuid.UUID,
    set_id: uuid.UUID,
    *,
    mode: str,
    triggered_by: str,
    assistant: str | int = "draft",
    created_by: str | None = None,
    admin_initiated: bool = False,
) -> EvalRun:
    """Queue a golden run (no cost confirmation: callers decide; setup uses mode "fast" and,
    for answers, shows the estimate in its own step before calling with mode "full")."""
    busy = (
        await session.execute(
            select(EvalRun.id).where(
                EvalRun.set_id == set_id,
                EvalRun.status.in_(("queued", "running")),
                EvalRun.started_at > stale_cutoff(),
            )
        )
    ).scalar_one_or_none()
    if busy is not None:
        from callobuzz_rag.api.errors import APIError

        raise APIError(
            409, "This golden set is already running", "eval_run_in_progress", run_id=str(busy)
        )
    run = EvalRun(
        id=uuid.uuid4(),
        corpus_id=corpus_id,
        kind="golden",
        set_id=set_id,
        mode=mode,
        target=str(assistant),
        triggered_by=triggered_by,
        status="queued",
        sample_size=0,
        config_snapshot={"assistant": assistant}
        | ({ADMIN_INITIATED: True} if admin_initiated else {}),
        thresholds={},
        criteria=[],
        created_by=created_by,
    )
    session.add(run)
    await session.commit()
    _dispatch(run.id)
    return run
