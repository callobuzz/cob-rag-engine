"""Assistant configuration of a knowledge base (conversation layer C2).

Any key that reaches the knowledge base may read, preview and edit the draft (members too);
publishing, restoring and switching on or off are owner-only (A5 `require_owner`). For an API
key, reads need the `read` permission and everything else `write` (`api/permissions.py`):
discarding a draft removes only unpublished work, and every publish is reversible by a restore.
The audit rows and `created_by` name the named key an agent acted with.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.analytics.usage import SOURCE_ASSISTANT, payer_of, record_usage
from callobuzz_rag.api.deps import get_session, path_corpus, require_owner, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.assistant import (
    AssistantStateResponse,
    AssistantVersionListResponse,
    AssistantVersionResponse,
    AssistantVersionSummary,
    CompileRequest,
    CompileResponse,
    CompileSectionOut,
    FromTemplateRequest,
    ImproveRuleRequest,
    ImproveRuleResponse,
    PatchDraftRequest,
    PublishRequest,
    PutDraftRequest,
    SetEnabledRequest,
    TemplateDetail,
    TemplateSummary,
)
from callobuzz_rag.billing.context import is_admin_request, mark_admin_answer
from callobuzz_rag.conversation.business_card import dispatch_business_card
from callobuzz_rag.conversation.compiler import (
    MAX_ENABLED_RULES,
    RULES_TOKEN_BUDGET,
    CompileTurn,
    compile_prompt,
    rules_section_tokens,
    worst_case_prompt_tokens,
)
from callobuzz_rag.conversation.schema import CURRENT_SCHEMA_VERSION
from callobuzz_rag.conversation.store import (
    AssistantConfigStore,
    default_config_for,
    get_runtime_config,
    prompt_budget,
    row_config,
)
from callobuzz_rag.conversation.templates import TEMPLATE_KEYS, list_templates, load_template
from callobuzz_rag.conversation.types import OfficeState, script_of
from callobuzz_rag.db.models.assistant_config import AssistantConfigVersion
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.llm.cost import request_cost_of

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/corpora/{corpus_id}/assistant", tags=["assistant"])
templates_router = APIRouter(prefix="/api/v1/assistant/templates", tags=["assistant"])


def _out(row: AssistantConfigVersion) -> AssistantVersionResponse:
    return AssistantVersionResponse(
        id=row.id,
        corpus_id=row.corpus_id,
        version=row.version,
        status=row.status,
        config=row.config,
        schema_version=row.schema_version,
        draft_revision=row.draft_revision,
        created_by=row.created_by,
        created_at=row.created_at,
        published_at=row.published_at,
        published_by=row.published_by_label,
        note=row.note,
        restored_from=row.restored_from,
    )


def _store(request: Request, session: AsyncSession) -> AssistantConfigStore:
    return AssistantConfigStore(session, request, request.app.state.settings)


async def _commit(session: AsyncSession, row: AssistantConfigVersion) -> AssistantVersionResponse:
    """Commit, then read the row back: its id and timestamps are server-generated."""
    await session.commit()
    await session.refresh(row)
    return _out(row)


async def _commit_and_dispatch(
    request: Request,
    session: AsyncSession,
    store: AssistantConfigStore,
    corpus: Corpus,
    row: AssistantConfigVersion,
) -> AssistantVersionResponse:
    out = await _commit(session, row)
    if store.pending_card is not None:
        try:
            await dispatch_business_card(
                session, corpus, store.pending_card, request.app.state.vector_store
            )
        except Exception:
            logger.warning(
                "Could not queue the business card for corpus %s", corpus.id, exc_info=True
            )
    # A published version gets a free behaviour check of every test set that has one
    # (conversation C7). Every writer of a published version comes through here. Skipped when
    # the assistant is switched off: there is no assistant behaviour to check.
    if row.version is not None and row.config.get("enabled", True):
        try:
            from callobuzz_rag.evaluation.golden_runner import enqueue_publish_runs

            await enqueue_publish_runs(
                session, corpus.id, row.version, admin_initiated=is_admin_request(request)
            )
        except Exception:
            logger.warning("Publish test runs not queued for %s", corpus.id, exc_info=True)
    return out


@router.get("", response_model=AssistantStateResponse)
async def get_assistant(
    corpus_id: UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> AssistantStateResponse:
    """The published assistant and the draft being edited (either may be null)."""
    published, draft = await _store(request, session).state(corpus.id)
    enabled = bool(published is not None and published.config.get("enabled", True))
    return AssistantStateResponse(
        corpus_id=corpus.id,
        enabled=enabled,
        published=_out(published) if published else None,
        draft=_out(draft) if draft else None,
        schema_version=CURRENT_SCHEMA_VERSION,
    )


@router.put("/draft", response_model=AssistantVersionResponse)
async def put_draft(
    corpus_id: UUID,
    body: PutDraftRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> AssistantVersionResponse:
    """Replace the draft. `base_revision` must be the revision you edited (409 otherwise)."""
    row = await _store(request, session).save_draft(corpus, body.config, body.base_revision)
    return await _commit(session, row)


@router.patch("/draft", response_model=AssistantVersionResponse)
async def patch_draft(
    corpus_id: UUID,
    body: PatchDraftRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> AssistantVersionResponse:
    """Merge a JSON merge patch (RFC 7396) into the draft, creating it from the published
    config when none exists."""
    row = await _store(request, session).patch_draft(corpus, body.patch, body.base_revision)
    return await _commit(session, row)


@router.post("/draft/from-template", response_model=AssistantVersionResponse)
async def draft_from_template(
    corpus_id: UUID,
    body: FromTemplateRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> AssistantVersionResponse:
    """Start the draft from one of the eight business templates."""
    row = await _store(request, session).draft_from_template(
        corpus, body.template, body.keep_identity, body.base_revision
    )
    return await _commit(session, row)


@router.delete("/draft", status_code=204)
async def discard_draft(
    corpus_id: UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Throw the draft away; the published assistant and its history are untouched."""
    await _store(request, session).discard_draft(corpus)
    await session.commit()


@router.post("/publish", response_model=AssistantVersionResponse)
async def publish(
    corpus_id: UUID,
    body: PublishRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> AssistantVersionResponse:
    """Publish the draft as the next version (owner only)."""
    store = _store(request, session)
    row = await store.publish(corpus, note=body.note, base_revision=body.base_revision)
    return await _commit_and_dispatch(request, session, store, corpus, row)


@router.get("/versions", response_model=AssistantVersionListResponse)
async def list_versions(
    corpus_id: UUID,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> AssistantVersionListResponse:
    """Published and archived versions, newest first."""
    rows, total = await _store(request, session).list_versions(corpus.id, offset, limit)
    items = [
        AssistantVersionSummary(
            version=r.version or 0,
            status=r.status,
            enabled=bool(r.config.get("enabled", True)),
            created_by=r.created_by,
            published_at=r.published_at,
            note=r.note,
            restored_from=r.restored_from,
        )
        for r in rows
    ]
    return AssistantVersionListResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/versions/{version}", response_model=AssistantVersionResponse)
async def get_version(
    corpus_id: UUID,
    version: int,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> AssistantVersionResponse:
    return _out(await _store(request, session).get_version(corpus.id, version))


@router.post("/versions/{version}/restore", response_model=AssistantVersionResponse)
async def restore_version(
    corpus_id: UUID,
    version: int,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> AssistantVersionResponse:
    """Publish an older version again as a new version (owner only; history is never
    rewritten)."""
    store = _store(request, session)
    row = await store.restore(corpus, version)
    return await _commit_and_dispatch(request, session, store, corpus, row)


@router.post("/enabled", response_model=AssistantVersionResponse)
async def set_enabled(
    corpus_id: UUID,
    body: SetEnabledRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> AssistantVersionResponse:
    """Switch the assistant on or off by publishing a new version (owner only). Off means
    every surface answers exactly as it did before the assistant existed."""
    store = _store(request, session)
    row = await store.set_enabled(corpus, body.enabled)
    return await _commit_and_dispatch(request, session, store, corpus, row)


@router.post("/compile", response_model=CompileResponse)
async def compile_assistant(
    corpus_id: UUID,
    body: CompileRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> CompileResponse:
    """The prompt the model would receive for a sample turn. A pure preview: no model call,
    nothing is saved. Compiles `config` when given, else the draft, else the published one."""
    config = body.config
    if config is None:
        published, draft = await _store(request, session).state(corpus.id)
        row = draft or published
        config = row_config(row) if row is not None else default_config_for(corpus)
    s = body.sample
    out = compile_prompt(
        config,
        CompileTurn(
            label=s.label,
            route=s.route,
            language=s.language,
            script=s.script or script_of(s.language),
            office=OfficeState(s.office_hours),
            page_url=s.page_url,
            topic_id=s.topic,
            lead_captured=s.lead_captured,
            one_off_instructions=s.one_off_instructions,
        ),
    )
    return CompileResponse(
        system_prompt=out.system_prompt,
        constraints=out.constraints,
        sections=[
            CompileSectionOut(name=x.name, text=x.text, tokens=x.tokens) for x in out.sections
        ],
        rules_fired=out.rules_fired,
        total_tokens=out.total_tokens,
        max_tokens=out.max_tokens,
        enabled_rules=sum(1 for r in config.rules if r.enabled),
        max_enabled_rules=MAX_ENABLED_RULES,
        rules_tokens=rules_section_tokens(config),
        rules_budget=RULES_TOKEN_BUDGET,
        prompt_tokens=worst_case_prompt_tokens(config),
        prompt_budget=prompt_budget(request.app.state.settings),
    )


@templates_router.get("", response_model=list[TemplateSummary])
async def templates(request: Request) -> list[TemplateSummary]:
    """The eight business templates."""
    resolve_scope(request)  # any authenticated principal; 401 otherwise
    return [
        TemplateSummary(
            key=t.key,
            name=t.name,
            description=t.description,
            business_type=t.config.identity.business_type,
        )
        for t in list_templates()
    ]


@templates_router.get("/{key}", response_model=TemplateDetail)
async def template(key: str, request: Request) -> TemplateDetail:
    """One template: the full config and its ten test questions."""
    resolve_scope(request)
    if key not in TEMPLATE_KEYS:
        raise APIError(404, f"No template named '{key}'", "template_not_found")
    t = load_template(key)
    return TemplateDetail(
        key=t.key,
        name=t.name,
        description=t.description,
        config=t.config.model_dump(mode="json"),
        tests=t.tests,
    )


@router.post("/rules/improve", response_model=ImproveRuleResponse)
async def improve_rule_route(
    corpus_id: UUID,
    body: ImproveRuleRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ImproveRuleResponse:
    """Suggest a cleaner wording for a guidance rule and name rules it may conflict with.

    Nothing is saved: the dashboard writes the suggestion into the draft only when the person
    accepts it. Uses the knowledge base's own model (its fast model when set). It spends that
    model, so it needs `write` permission; the spend is metered under source `assistant` and
    never counts as a question. There is no rate limit besides the metering.
    """
    from callobuzz_rag.api.routes.query import _get_config_resolver
    from callobuzz_rag.conversation.improve import improve_rule
    from callobuzz_rag.core.exceptions import LLMError
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

    # PIP1 profile: the helper call uses the model the business's profile names
    from callobuzz_rag.tiers import profiles as tier_profiles
    from callobuzz_rag.tiers.resolve import resolve_with_profile

    engine_settings = request.app.state.settings
    resolved, _retrieval = resolve_with_profile(
        _get_config_resolver(request),
        corpus,
        headers=None,
        profile=await tier_profiles.plan_profile(
            session, corpus.tenant_id, settings=engine_settings
        ),
        settings=engine_settings,
    )
    # /PIP1 profile
    llm = LiteLLMProvider.from_resolved_config(resolved)
    if not llm.has_credentials:
        raise APIError(
            503,
            "No AI model is set up for this knowledge base, so rules can't be improved.",
            "assistant_llm_unavailable",
        )
    runtime = await get_runtime_config(session, corpus.id, use_draft=True)
    rules = runtime.config.rules if runtime is not None else []
    others = [r for r in rules if r.enabled and r.id != body.rule_id]
    failed = False
    result: ImproveRuleResponse | None = None
    try:
        result = await improve_rule(
            llm, instruction=body.instruction, rule_type=body.type, others=others
        )
    except LLMError:
        logger.warning("Improve-rule model call failed for %s", corpus.id, exc_info=True)
        failed = True
    cost = request_cost_of(llm)
    if cost is not None:
        mark_admin_answer(request)  # BIL3: the operator's helper calls are not billed
        # Spend, not a question: requests=0 keeps it off the monthly count.
        await record_usage(
            session,
            corpus_id=corpus.id,
            tenant_id=corpus.tenant_id,
            source=SOURCE_ASSISTANT,
            prompt_tokens=0,
            completion_tokens=0,
            model=getattr(resolved, "model", None),
            requests=0,
            cost=cost,
            payer=payer_of(resolved),
        )
    if failed or result is None:
        raise APIError(502, "The AI model did not answer. Try again.", "assistant_llm_failed")
    return result
