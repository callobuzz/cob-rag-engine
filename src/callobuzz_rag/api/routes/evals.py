"""Golden sets, criteria and evaluation settings of one knowledge base (conversation C7)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, path_corpus, require_owner
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.evals import (
    AddPairsRequest,
    CompareRequest,
    ComparisonOut,
    CriterionIn,
    CriterionList,
    CriterionOut,
    CriterionUpdate,
    EstimateOut,
    EstimateRequest,
    EvalSettingsIn,
    EvalSettingsOut,
    FromTemplateRequest,
    GoldenPairFields,
    GoldenPairOut,
    GoldenSetCreate,
    GoldenSetDetail,
    GoldenSetList,
    GoldenSetOut,
    GoldenSetUpdate,
    LastRun,
    RateSampleRequest,
    RunCreate,
    RunDetail,
    RunList,
    RunSummary,
    SampleList,
    SampleOut,
)
from callobuzz_rag.billing.context import is_admin_request
from callobuzz_rag.db.models import (
    Corpus,
    EvalCriterion,
    EvalRun,
    EvalRunSample,
    EvalSettings,
    GoldenPair,
    GoldenSet,
)
from callobuzz_rag.evaluation.golden import pair_from_fields
from callobuzz_rag.evaluation.metrics import ALL_METRICS, CRITERION_PREFIX, DEFAULT_THRESHOLDS
from callobuzz_rag.evaluation.optional import ADDON_MESSAGE, ragas_available

router = APIRouter(prefix="/api/v1/corpora/{corpus_id}", tags=["evaluation"])


def _limit(limit: int) -> APIError:
    return APIError(
        422, f"A golden set holds at most {limit} questions", "golden_set_limit", limit=limit
    )


async def load_set(session: AsyncSession, corpus_id: uuid.UUID, set_id: uuid.UUID) -> GoldenSet:
    row = (
        await session.execute(
            select(GoldenSet).where(GoldenSet.id == set_id, GoldenSet.corpus_id == corpus_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise APIError(404, "Golden set not found", "golden_set_not_found")
    return row


def _set_out(
    row: GoldenSet, pairs: int, behaviour: int, last: EvalRun | None = None
) -> GoldenSetOut:
    out = GoldenSetOut.model_validate(row)
    out.pair_count, out.behaviour_count = pairs, behaviour
    if last is not None:
        totals = last.totals or {}
        out.last_run = LastRun(
            id=last.id,
            status=last.status,
            mode=last.mode,
            passed=int(totals.get("passed", 0)),
            total=int(totals.get("questions", 0)),
            finished_at=last.completed_at,
        )
    return out


async def _create(
    session: AsyncSession,
    request: Request,
    corpus: Corpus,
    name: str,
    source: str,
    pairs: list[GoldenPairFields],
) -> GoldenSetOut:
    limit = request.app.state.settings.golden_set_max_pairs
    if len(pairs) > limit:
        raise _limit(limit)
    row = GoldenSet(
        id=uuid.uuid4(), corpus_id=corpus.id, name=name, source=source, is_default=False
    )
    session.add(row)
    await session.flush()  # pairs reference the set and no relationship orders the inserts
    for position, p in enumerate(pairs):
        session.add(pair_from_fields(corpus.id, row.id, p, position))
    await session.commit()
    await session.refresh(row)
    return _set_out(row, len(pairs), sum(1 for p in pairs if p.expected_behaviour))


@router.get("/golden-sets", response_model=GoldenSetList)
async def list_sets(
    corpus: Corpus = Depends(path_corpus), session: AsyncSession = Depends(get_session)
) -> GoldenSetList:
    rows = (
        (
            await session.execute(
                select(GoldenSet)
                .where(GoldenSet.corpus_id == corpus.id)
                .order_by(GoldenSet.created_at)
            )
        )
        .scalars()
        .all()
    )
    counts = {
        set_id: (int(total), int(behaviour))
        for set_id, total, behaviour in (
            await session.execute(
                select(
                    GoldenPair.set_id,
                    func.count(),
                    func.count().filter(GoldenPair.expected_behaviour.is_not(None)),
                )
                .where(GoldenPair.corpus_id == corpus.id)
                .group_by(GoldenPair.set_id)
            )
        ).all()
    }
    last: dict[uuid.UUID, EvalRun] = {}
    runs = (
        (
            await session.execute(
                select(EvalRun)
                .where(EvalRun.corpus_id == corpus.id, EvalRun.kind == "golden")
                .order_by(EvalRun.started_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    for run in runs:
        if run.set_id is not None:
            last.setdefault(run.set_id, run)
    items = [_set_out(r, *counts.get(r.id, (0, 0)), last.get(r.id)) for r in rows]
    return GoldenSetList(items=items, count=len(items))


@router.post("/golden-sets", response_model=GoldenSetOut, status_code=201)
async def create_set(
    body: GoldenSetCreate,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> GoldenSetOut:
    return await _create(session, request, corpus, body.name, "owner", body.pairs)


@router.post("/golden-sets/from-template", response_model=GoldenSetOut, status_code=201)
async def create_from_template(
    body: FromTemplateRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> GoldenSetOut:
    from callobuzz_rag.evaluation.templates import template_pairs

    key: str = body.template or "generic_support"
    if body.template is None:
        from callobuzz_rag.conversation.store import get_runtime_config

        runtime = await get_runtime_config(session, corpus.id)
        if runtime is not None:
            key = runtime.config.identity.business_type
    try:
        pairs = template_pairs(key)
    except KeyError:
        raise APIError(404, "Template not found", "template_not_found") from None
    return await _create(session, request, corpus, f"Template questions ({key})", "template", pairs)


@router.get("/golden-sets/{set_id}", response_model=GoldenSetDetail)
async def get_set(
    set_id: uuid.UUID,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> GoldenSetDetail:
    row = await load_set(session, corpus.id, set_id)
    pairs = (
        (
            await session.execute(
                select(GoldenPair)
                .where(GoldenPair.set_id == set_id)
                .order_by(GoldenPair.position, GoldenPair.created_at)
            )
        )
        .scalars()
        .all()
    )
    base = _set_out(row, len(pairs), sum(1 for p in pairs if p.expected_behaviour))
    return GoldenSetDetail(
        **base.model_dump(), pairs=[GoldenPairOut.model_validate(p) for p in pairs]
    )


@router.patch("/golden-sets/{set_id}", response_model=GoldenSetOut)
async def rename_set(
    set_id: uuid.UUID,
    body: GoldenSetUpdate,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> GoldenSetOut:
    row = await load_set(session, corpus.id, set_id)
    row.name = body.name
    await session.commit()
    await session.refresh(row)
    return GoldenSetOut.model_validate(row)


@router.delete("/golden-sets/{set_id}", status_code=204)
async def delete_set(
    set_id: uuid.UUID,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> None:
    row = await load_set(session, corpus.id, set_id)
    if row.is_default:
        raise APIError(409, "The default set cannot be deleted", "golden_set_is_default")
    await session.execute(delete(GoldenSet).where(GoldenSet.id == set_id))
    await session.commit()


@router.post("/golden-sets/{set_id}/pairs", response_model=list[GoldenPairOut], status_code=201)
async def add_pairs(
    set_id: uuid.UUID,
    body: AddPairsRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> list[GoldenPairOut]:
    await load_set(session, corpus.id, set_id)
    limit = request.app.state.settings.golden_set_max_pairs
    existing = int(
        (await session.execute(select(func.count()).where(GoldenPair.set_id == set_id))).scalar()
        or 0
    )
    if existing + len(body.pairs) > limit:
        raise _limit(limit)
    rows = [pair_from_fields(corpus.id, set_id, p, existing + i) for i, p in enumerate(body.pairs)]
    for row in rows:
        session.add(row)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return [GoldenPairOut.model_validate(r) for r in rows]


# Single pairs are edited and deleted through the existing /api/v1/golden/{pair_id} routes.


@router.get("/eval/criteria", response_model=CriterionList)
async def list_criteria(
    corpus: Corpus = Depends(path_corpus), session: AsyncSession = Depends(get_session)
) -> CriterionList:
    rows = (
        (
            await session.execute(
                select(EvalCriterion)
                .where(EvalCriterion.corpus_id == corpus.id)
                .order_by(EvalCriterion.name)
            )
        )
        .scalars()
        .all()
    )
    return CriterionList(items=[CriterionOut.model_validate(r) for r in rows])


@router.post("/eval/criteria", response_model=CriterionOut, status_code=201)
async def create_criterion(
    body: CriterionIn,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> CriterionOut:
    taken = (
        await session.execute(
            select(EvalCriterion.id).where(
                EvalCriterion.corpus_id == corpus.id, EvalCriterion.name == body.name
            )
        )
    ).scalar_one_or_none()
    if taken is not None:
        raise APIError(409, "A criterion with this name exists", "eval_criterion_exists")
    row = EvalCriterion(corpus_id=corpus.id, **body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return CriterionOut.model_validate(row)


async def _criterion(
    session: AsyncSession, corpus_id: uuid.UUID, criterion_id: uuid.UUID
) -> EvalCriterion:
    row = (
        await session.execute(
            select(EvalCriterion).where(
                EvalCriterion.id == criterion_id, EvalCriterion.corpus_id == corpus_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise APIError(404, "Criterion not found", "eval_criterion_not_found")
    return row


@router.patch("/eval/criteria/{criterion_id}", response_model=CriterionOut)
async def update_criterion(
    criterion_id: uuid.UUID,
    body: CriterionUpdate,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> CriterionOut:
    row = await _criterion(session, corpus.id, criterion_id)
    for field in body.model_fields_set:
        value: Any = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return CriterionOut.model_validate(row)


@router.delete("/eval/criteria/{criterion_id}", status_code=204)
async def delete_criterion(
    criterion_id: uuid.UUID,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _criterion(session, corpus.id, criterion_id)
    await session.execute(delete(EvalCriterion).where(EvalCriterion.id == criterion_id))
    await session.commit()


def _settings_out(row: EvalSettings | None) -> EvalSettingsOut:
    thresholds = dict(row.thresholds or {}) if row else {}
    effective = DEFAULT_THRESHOLDS.with_overrides(thresholds)
    return EvalSettingsOut(
        judge_provider=row.judge_provider if row else None,
        judge_model=row.judge_model if row else None,
        judge_has_key=bool(row and row.judge_api_key_encrypted),
        judge_base_url=row.judge_base_url if row else None,
        thresholds=thresholds,
        effective_thresholds={m: effective.get(m) for m in ALL_METRICS},
    )


@router.get("/eval/settings", response_model=EvalSettingsOut)
async def get_eval_settings(
    corpus: Corpus = Depends(path_corpus), session: AsyncSession = Depends(get_session)
) -> EvalSettingsOut:
    return _settings_out(await session.get(EvalSettings, corpus.id))


@router.put("/eval/settings", response_model=EvalSettingsOut)
async def put_eval_settings(
    body: EvalSettingsIn,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> EvalSettingsOut:
    from callobuzz_rag.core.secrets import encryptor_from_settings

    unknown = sorted(set(body.thresholds) - set(ALL_METRICS))
    if unknown or any(not 0.0 <= v <= 1.0 for v in body.thresholds.values()):
        raise APIError(
            422,
            f"Unknown metric or threshold outside 0..1: {', '.join(unknown) or 'value'}",
            "eval_thresholds_invalid",
        )
    row = await session.get(EvalSettings, corpus.id)
    if row is None:
        row = EvalSettings(corpus_id=corpus.id, thresholds={})
        session.add(row)
    if body.judge_base_url and not (
        body.judge_api_key or (body.judge_api_key is None and row.judge_api_key_encrypted)
    ):
        raise APIError(
            422,
            "A custom judge host needs its own judge key: the knowledge base key is never sent there",
            "eval_judge_key_required",
        )
    row.judge_provider = body.judge_provider
    row.judge_model = body.judge_model
    row.judge_base_url = body.judge_base_url
    row.thresholds = dict(body.thresholds)
    if body.judge_api_key == "":
        row.judge_api_key_encrypted = None
    elif body.judge_api_key is not None:
        encryptor = encryptor_from_settings(request.app.state.settings)
        if encryptor is None:
            raise APIError(
                503, "Set RAG_ENCRYPTION_KEY to store a judge key", "encryption_not_configured"
            )
        row.judge_api_key_encrypted = encryptor.encrypt(body.judge_api_key)
    await session.commit()
    return _settings_out(row)


# --- runs, samples, estimates and comparison (conversation C7) ---


async def _estimate(
    session: AsyncSession, request: Request, corpus: Corpus, body: EstimateRequest
) -> tuple[Any, str, str]:
    from callobuzz_rag.core.config_resolver import ConfigResolver
    from callobuzz_rag.core.secrets import encryptor_from_settings
    from callobuzz_rag.evaluation.cost import estimate_run_cost
    from callobuzz_rag.evaluation.golden_runner import judge_config_for

    settings = request.app.state.settings
    count_stmt = select(func.count()).where(GoldenPair.set_id == body.set_id)
    if body.mode == "fast":
        count_stmt = count_stmt.where(GoldenPair.expected_behaviour.is_not(None))
    questions = int((await session.execute(count_stmt)).scalar() or 0)
    # PIP1 profile: the estimate prices the model the run will really answer with
    from callobuzz_rag.tiers import profiles as tier_profiles
    from callobuzz_rag.tiers.resolve import resolve_with_profile

    answer, _retrieval = resolve_with_profile(
        ConfigResolver(settings, encryptor=encryptor_from_settings(settings)),
        corpus,
        headers=None,
        profile=await tier_profiles.plan_profile(session, corpus.tenant_id, settings=settings),
        settings=settings,
    )
    # /PIP1 profile
    chosen = body.settings.answer_model
    answer_model = chosen.model if chosen else answer.model
    answer_provider = chosen.provider if chosen else answer.provider
    judge = await judge_config_for(session, corpus, settings)
    criteria = int(
        (
            await session.execute(
                select(func.count()).where(
                    EvalCriterion.corpus_id == corpus.id, EvalCriterion.enabled.is_(True)
                )
            )
        ).scalar()
        or 0
    )
    estimate = estimate_run_cost(
        questions,
        body.mode,
        answer_model,
        judge.model,
        len(ALL_METRICS) if ragas_available() else 0,  # RAGAS metrics need the add-on
        criteria,
        answer_provider=answer_provider,
        judge_provider=judge.provider,
    )
    return estimate, answer_model, judge.model


@router.post("/eval/estimate", response_model=EstimateOut)
async def estimate(
    body: EstimateRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> EstimateOut:
    await load_set(session, corpus.id, body.set_id)
    est, answer_model, judge_model = await _estimate(session, request, corpus, body)
    return EstimateOut(**est.__dict__, answer_model=answer_model, judge_model=judge_model)


async def _check_snapshot(
    session: AsyncSession, request: Request, corpus: Corpus, body: EstimateRequest
) -> None:
    """422 at creation, not a failed run later: unusable retrieval settings or a version
    that does not exist."""
    from callobuzz_rag.conversation.store import AssistantConfigStore
    from callobuzz_rag.conversation.testing.director_port import (
        InvalidSnapshotError,
        retrieval_overrides,
    )

    settings = request.app.state.settings
    try:
        retrieval_overrides(body.settings, settings)
    except InvalidSnapshotError as exc:
        raise APIError(422, str(exc), "eval_settings_invalid") from exc
    assistant = body.settings.assistant
    if isinstance(assistant, int):
        await AssistantConfigStore(session, request, settings).get_version(corpus.id, assistant)


async def _check_question_room(
    session: AsyncSession, request: Request, corpus: Corpus, questions: int
) -> None:
    """Every question of a full run is a question against the monthly cap: refuse up front
    rather than stop half way. Same 429 as any answering route."""
    from callobuzz_rag.accounts import caps

    # BIL4 billing: golden runs are charged questions, refused before the run starts; the payer
    # is the knowledge base's own stored key (BYOK) when it has one, else the operator
    from callobuzz_rag.billing.enforcement import payer_hint
    from callobuzz_rag.fairuse.gate import answer_gate, refuse_measured  # PIP3 gate

    # P5 plans: a stored key pays only on a plan that includes it (shared with the gate)
    from callobuzz_rag.tiers import profiles as tier_profiles

    plan = await tier_profiles.plan_profile(
        session, corpus.tenant_id, settings=request.app.state.settings
    )
    payer = payer_hint(corpus, None, use_headers=False, byok_allowed=plan.byok_allowed)
    # /P5 plans
    # PIP3 gate: a golden run is never downgraded (D10: a measurement must not change because
    # the business is being throttled), but a paused workspace cannot start one: 402 or 429
    decision = await answer_gate(
        session,
        request,
        corpus,
        payer=payer,
        surface="eval",
        count_question=False,
        plan_profile=plan,  # P5 plans
    )
    await refuse_measured(decision, request)
    # /PIP3 gate
    # /BIL4 billing
    if getattr(request.state, "is_admin", False):
        return
    if await caps.overage_lets_through(session, corpus.tenant_id, payer):  # BIL4 overage
        return
    moment = datetime.now(UTC)
    quota = await caps.question_quota(session, corpus.tenant_id, caps.current_month(moment))
    if quota is None:
        return
    limit, used = quota
    if used + questions > limit:
        raise APIError(
            429,
            f"This run needs {questions} questions but only {max(limit - used, 0)} are left "
            "this month",
            "question_cap_reached",
            limit=limit,
            used=used,
            resets_at=caps.next_month_start(moment).isoformat(),
        )


@router.post("/eval/runs", response_model=RunSummary, status_code=202)
async def start_run(
    body: RunCreate,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> RunSummary:
    from callobuzz_rag.accounts.audit import actor_from_request
    from callobuzz_rag.evaluation.golden_runner import (
        ADMIN_INITIATED,
        _dispatch,
        judge_config_for,
        stale_cutoff,
    )

    await load_set(session, corpus.id, body.set_id)
    busy = (
        await session.execute(
            select(EvalRun.id).where(
                EvalRun.set_id == body.set_id,
                EvalRun.status.in_(("queued", "running")),
                EvalRun.started_at > stale_cutoff(),
            )
        )
    ).scalar_one_or_none()
    if busy is not None:
        raise APIError(
            409, "This golden set is already running", "eval_run_in_progress", run_id=str(busy)
        )
    await _check_snapshot(session, request, corpus, body)
    estimated: float | None = 0.0
    if body.mode == "full" and not ragas_available():
        # Without the add-on a full run still answers, checks behaviour and judges your own
        # criteria; with nothing of those to score it would measure nothing.
        enabled = (
            await session.execute(
                select(func.count()).where(
                    EvalCriterion.corpus_id == corpus.id, EvalCriterion.enabled.is_(True)
                )
            )
        ).scalar()
        if not enabled:
            raise APIError(501, ADDON_MESSAGE, "evaluation_addon_missing")
    if body.mode == "full":
        judge = await judge_config_for(session, corpus, request.app.state.settings)
        if not judge.api_key and not judge.base_url:
            raise APIError(
                503,
                "No LLM credentials to judge with: give the knowledge base an LLM key or set "
                "a judge key",
                "eval_judge_unavailable",
            )
        est, _, _ = await _estimate(session, request, corpus, body)
        estimated = round(est.total_usd, 6)
        if body.confirm_cost_usd is None or body.confirm_cost_usd + 1e-9 < estimated:
            raise APIError(
                409,
                "Confirm the estimated cost to start this run",
                "eval_cost_unconfirmed",
                estimated_cost_usd=estimated,
            )
        await _check_question_room(session, request, corpus, est.questions)
    settings_row = await session.get(EvalSettings, corpus.id)
    criteria = (
        (
            await session.execute(
                select(EvalCriterion).where(
                    EvalCriterion.corpus_id == corpus.id, EvalCriterion.enabled.is_(True)
                )
            )
        )
        .scalars()
        .all()
        if body.mode == "full"
        else []
    )
    thresholds: dict[str, float] = dict(settings_row.thresholds or {}) if settings_row else {}
    thresholds.update({CRITERION_PREFIX + c.name: c.threshold for c in criteria})
    run = EvalRun(
        id=uuid.uuid4(),
        corpus_id=corpus.id,
        kind="golden",
        name=body.name,
        set_id=body.set_id,
        mode=body.mode,
        target=str(body.settings.assistant),
        triggered_by="manual",
        status="queued",
        sample_size=0,
        config_snapshot=body.settings.model_dump(mode="json")
        | ({ADMIN_INITIATED: True} if is_admin_request(request) else {}),
        estimated_cost_usd=estimated,
        thresholds=thresholds,
        criteria=[
            {"name": c.name, "definition": c.definition, "threshold": c.threshold} for c in criteria
        ],
        created_by=actor_from_request(request)[2][:320],
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    try:
        _dispatch(run.id)
    except Exception as exc:
        run.status = "failed"
        run.error_message = "Could not queue the run: the task broker is unavailable"
        await session.commit()
        raise HTTPException(
            status_code=503, detail="Task broker unavailable; the run was not queued"
        ) from exc
    return RunSummary.model_validate(run, from_attributes=True)


@router.get("/eval/runs", response_model=RunList)
async def list_runs(
    set_id: uuid.UUID | None = Query(None),
    kind: str | None = Query(None, pattern="^(traffic|golden)$"),
    limit: int = Query(20, ge=1, le=100),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> RunList:
    stmt = select(EvalRun).where(EvalRun.corpus_id == corpus.id)
    if set_id is not None:
        stmt = stmt.where(EvalRun.set_id == set_id)
    if kind is not None:
        stmt = stmt.where(EvalRun.kind == kind)
    runs = (
        (await session.execute(stmt.order_by(EvalRun.started_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return RunList(
        items=[RunSummary.model_validate(r, from_attributes=True) for r in runs], count=len(runs)
    )


async def _run(session: AsyncSession, corpus_id: uuid.UUID, run_id: uuid.UUID) -> EvalRun:
    run = (
        await session.execute(
            select(EvalRun).where(EvalRun.id == run_id, EvalRun.corpus_id == corpus_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise APIError(404, "Evaluation run not found", "eval_run_not_found")
    return run


@router.get("/eval/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: uuid.UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> RunDetail:
    from callobuzz_rag.evaluation.compare import metric_summary, tag_breakdown
    from callobuzz_rag.evaluation.golden_runner import public_snapshot

    run = await _run(session, corpus.id, run_id)
    # Only what the summary reads: answers and contexts stay out of a poll every few seconds.
    samples = (
        await session.execute(
            select(EvalRunSample.scores, EvalRunSample.tags, EvalRunSample.passed).where(
                EvalRunSample.run_id == run_id
            )
        )
    ).all()
    settings = request.app.state.settings
    summary = metric_summary(
        list(samples),
        run.thresholds or {},
        iterations=settings.eval_bootstrap_iterations,
        min_samples=settings.eval_min_samples,
    )
    warnings = (
        [f"Only {len(samples)} questions: confidence intervals are wide."]
        if 0 < len(samples) < settings.eval_min_samples
        else []
    )
    return RunDetail(
        **RunSummary.model_validate(run, from_attributes=True).model_dump(),
        settings=public_snapshot(run.config_snapshot),
        metrics=summary,
        tags=tag_breakdown(list(samples)),
        warnings=warnings,
    )


@router.get("/eval/runs/{run_id}/samples", response_model=SampleList)
async def list_samples(
    run_id: uuid.UUID,
    failed_only: bool = Query(False),
    metric: str | None = Query(None, max_length=80),
    below: float | None = Query(None, ge=0, le=1),
    tag: str | None = Query(None, pattern="^(origin|difficulty|topic):.{1,60}$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SampleList:
    await _run(session, corpus.id, run_id)
    stmt = select(EvalRunSample).where(EvalRunSample.run_id == run_id)
    if failed_only:
        stmt = stmt.where(EvalRunSample.passed.is_(False))
    if metric is not None:
        stmt = stmt.where(EvalRunSample.scores.has_key(metric))
        if below is not None:
            stmt = stmt.where(EvalRunSample.scores[metric].as_float() < below)
    if tag is not None:
        name, _, tag_value = tag.partition(":")
        stmt = stmt.where(EvalRunSample.tags[name].as_string() == tag_value)
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    )
    rows = (
        (await session.execute(stmt.order_by(EvalRunSample.position).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return SampleList(
        items=[SampleOut.model_validate(r) for r in rows], count=len(rows), total=total
    )


@router.patch("/eval/samples/{sample_id}", response_model=SampleOut)
async def rate_sample(
    sample_id: uuid.UUID,
    body: RateSampleRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SampleOut:
    from callobuzz_rag.accounts.audit import actor_from_request

    row = (
        await session.execute(
            select(EvalRunSample)
            .join(EvalRun, EvalRun.id == EvalRunSample.run_id)
            .where(EvalRunSample.id == sample_id, EvalRun.corpus_id == corpus.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise APIError(404, "Evaluation sample not found", "eval_sample_not_found")
    row.rating = body.rating
    row.rated_by = actor_from_request(request)[2][:320] if body.rating else None
    await session.commit()
    await session.refresh(row)
    return SampleOut.model_validate(row)


@router.post("/eval/compare", response_model=ComparisonOut)
async def compare(
    body: CompareRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ComparisonOut:
    from callobuzz_rag.evaluation.compare import compare_runs

    found = (
        (
            await session.execute(
                select(EvalRun).where(EvalRun.id.in_(body.run_ids), EvalRun.corpus_id == corpus.id)
            )
        )
        .scalars()
        .all()
    )
    if len(set(body.run_ids)) != len(body.run_ids):
        raise APIError(422, "Compare different runs", "eval_compare_invalid")
    runs = sorted(found, key=lambda r: body.run_ids.index(r.id))
    if len(runs) != len(set(body.run_ids)):
        raise APIError(404, "Evaluation run not found", "eval_run_not_found")
    if (
        len({r.set_id for r in runs}) != 1
        or runs[0].set_id is None
        or any(r.kind != "golden" or r.status != "completed" for r in runs)
    ):
        raise APIError(422, "Compare completed runs of the same golden set", "eval_compare_invalid")
    samples: dict[uuid.UUID, list[Any]] = {}
    for r in runs:
        samples[r.id] = list(
            (
                await session.execute(
                    select(
                        EvalRunSample.golden_pair_id,
                        EvalRunSample.question,
                        EvalRunSample.scores,
                        EvalRunSample.tags,
                        EvalRunSample.passed,
                    ).where(EvalRunSample.run_id == r.id)
                )
            ).all()
        )
    settings = request.app.state.settings
    return compare_runs(
        runs,
        samples,
        iterations=settings.eval_bootstrap_iterations,
        min_samples=settings.eval_min_samples,
    )
