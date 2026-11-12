"""Corpus CRUD v2 endpoints — collection creation, key management, config encryption."""

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.api_keys import (
    DEFAULT_KEY_NAME,
    corpus_owner,
    create_key,
    rotate_default_key,
)
from callobuzz_rag.accounts.caps import check_kb_room
from callobuzz_rag.api.deps import (
    get_session,
    require_admin_or_tenant,
    require_owner,
    require_owner_for,
)
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.features import refuse_hidden_fields  # BIZ3 features
from callobuzz_rag.api.permissions import KeyInfo
from callobuzz_rag.api.schemas.corpus import (
    CorpusDetailResponse,
    CorpusListResponse,
    CorpusResponse,
    CorpusStats,
    CreateCorpusRequest,
    CreateCorpusResponse,
    EmbeddingConfigInput,
    LLMConfigInput,
    RetrievalConfigInput,
    RotateKeyResponse,
    UpdateCorpusRequest,
)
from callobuzz_rag.api.schemas.delete_preview import DeletePreviewResponse
from callobuzz_rag.billing.byok import refuse_key_save  # P5 plans
from callobuzz_rag.cob.system import refuse_system_corpus, refuse_system_tenant
from callobuzz_rag.conversation.business_card import dispatch_business_card
from callobuzz_rag.conversation.store import AssistantConfigStore, default_config_for
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.secrets import parse_key_list
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.lead import Lead

if TYPE_CHECKING:
    # Only ever used in local variable annotations, which Python does not
    # evaluate at runtime (PEP 526).
    from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/corpora", tags=["corpora"])


def _encrypt_llm_key(llm_config_dict: dict[str, Any], request: Request) -> dict[str, Any]:
    """Encrypt the api_key field in LLM config using Fernet, if present.

    Returns a new dict with api_key removed and api_key_encrypted added.
    """
    if not llm_config_dict or "api_key" not in llm_config_dict:
        return llm_config_dict

    api_key = llm_config_dict.get("api_key")
    if not api_key:
        return llm_config_dict

    from callobuzz_rag.core.secrets import FernetEncryptor

    settings = request.app.state.settings
    encryptor = FernetEncryptor(settings.encryption_key)
    encrypted = encryptor.encrypt(api_key)

    result = dict(llm_config_dict)
    del result["api_key"]
    result["api_key_encrypted"] = encrypted
    return result


def _encrypt_embedding_key(
    embedding_config_dict: dict[str, Any], request: Request
) -> dict[str, Any]:
    """Encrypt the api_key field in embedding config using Fernet."""
    if "api_key" not in embedding_config_dict or not embedding_config_dict["api_key"]:
        return embedding_config_dict

    from callobuzz_rag.core.secrets import FernetEncryptor

    settings = request.app.state.settings
    encryptor = FernetEncryptor(settings.encryption_key)
    encrypted = encryptor.encrypt(embedding_config_dict["api_key"])

    result = dict(embedding_config_dict)
    del result["api_key"]
    result["api_key_encrypted"] = encrypted
    return result


def _validated_embedding_config(
    body_config: EmbeddingConfigInput | None, request: Request
) -> dict[str, Any] | None:
    """An embedding config checked against the catalog, normalized, its key encrypted.

    The same rule for create and update: a named model must be in
    EMBEDDING_CATALOG (422 otherwise) and is stored with the catalog's provider,
    model and dimensions. None when nothing was sent.
    """
    if body_config is None:
        return None
    config = body_config.model_dump(exclude_none=True)
    if not config:
        return None
    model = config.get("model")
    provider = config.get("provider")
    if model:
        from callobuzz_rag.core.model_catalog import get_embedding_catalog_entry

        entry = get_embedding_catalog_entry(model, provider=provider)
        if entry is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown embedding model: provider='{provider}', model='{model}'",
            )
        config["provider"] = entry["provider"]
        config["model"] = entry["model"]
        config["dimensions"] = entry["dimensions"]
    if "api_key" in config:
        config = _encrypt_embedding_key(config, request)
    return config


def _redact_embedding_config(embedding_config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Redact the api_key_encrypted field in embedding config for API responses."""
    if not embedding_config:
        return embedding_config
    result = dict(embedding_config)
    if "api_key_encrypted" in result:
        result["api_key_encrypted"] = "***"
    return result


def _redact_llm_config(llm_config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Redact the api_key_encrypted field in LLM config for API responses."""
    if not llm_config:
        return llm_config

    result = dict(llm_config)
    if "api_key_encrypted" in result:
        result["api_key_encrypted"] = "***"
    return result


def _resolve_retrieval_config_input(
    body_config: RetrievalConfigInput | None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Resolve RetrievalConfigInput to a flat dict for DB storage.

    If preset is set, expand it first, then apply individual overrides on top.
    Validates merged result against bounds constraints.
    """
    if body_config is None:
        return None

    from callobuzz_rag.core.retrieval_presets import expand_preset, validate_retrieval_config

    result: dict[str, Any] = {}

    # Start from preset if specified
    if body_config.preset is not None:
        result = expand_preset(body_config.preset, settings)

    # Apply individual overrides (non-None fields only)
    overrides = body_config.model_dump(exclude={"preset"}, exclude_none=True)
    result.update(overrides)

    if not result:
        return None

    # Validate merged config against bounds
    errors = validate_retrieval_config(result)
    if errors:
        raise HTTPException(
            status_code=422, detail=f"Invalid retrieval config: {'; '.join(errors)}"
        )

    return result


def _merge_config(
    existing: dict[str, Any] | None, changes: dict[str, Any]
) -> dict[str, Any] | None:
    """Apply explicit changes to a stored config section.

    `changes` holds only the fields the caller actually sent (`exclude_unset`):
    a value sets the field, an explicit null removes it so the next tier (the
    engine default) applies again, and an omitted field is left alone. So `{}`
    changes nothing. An empty result is stored as NULL.
    """
    merged = dict(existing) if existing else {}
    for key, value in changes.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged or None


def _retrieval_config_changes(
    body_config: RetrievalConfigInput,
    existing: dict[str, Any] | None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """The stored retrieval config after an update: preset, then fields, then nulls.

    A preset expands to its values first; fields sent alongside it win; a field
    sent as null is removed (engine default again). The merged result is
    validated against the same bounds as before, so neither the admin nor the
    self-service route can store an out-of-range value.
    """
    from callobuzz_rag.core.retrieval_presets import expand_preset, validate_retrieval_config

    changes: dict[str, Any] = {}
    if body_config.preset is not None:
        changes.update(expand_preset(body_config.preset, settings))
    changes.update(body_config.model_dump(exclude={"preset"}, exclude_unset=True))

    merged = _merge_config(existing, changes)
    errors = validate_retrieval_config(merged or {})
    if errors:
        raise HTTPException(
            status_code=422, detail=f"Invalid retrieval config: {'; '.join(errors)}"
        )
    return merged


def _llm_config_changes(
    body_config: LLMConfigInput, existing: dict[str, Any] | None, request: Request
) -> dict[str, Any] | None:
    """The stored LLM config after an update, with the key encrypted or removed.

    `api_key: null` removes the stored encrypted key (the engine default key
    applies again); a new key replaces it, encrypted. An empty string is
    refused rather than guessed at: it could mean "unchanged" or "remove".
    """
    changes = body_config.model_dump(exclude_unset=True)
    if "api_key" in changes:
        api_key = changes.pop("api_key")
        if api_key == "":
            raise HTTPException(
                status_code=422,
                detail="llm_config.api_key is empty: send null to remove the stored key",
            )
        if api_key is None:
            changes["api_key_encrypted"] = None
        else:
            changes.update(_encrypt_llm_key({"api_key": api_key}, request))
    return _merge_config(existing, changes)


async def invalidate_corpus_query_cache(settings: Settings, corpus_id: str) -> None:
    """Invalidate the query response cache for a specific corpus.

    Shared by corpus admin routes and tenant self-service config routes.
    Silently logs failures (cache invalidation is best-effort).
    """
    try:
        from callobuzz_rag.retrieval.query_cache import QueryResponseCache

        cache = QueryResponseCache(settings)
        await cache.invalidate_corpus(corpus_id)
        await cache.close()
    except Exception:
        logger.warning("Failed to invalidate query cache for corpus %s", corpus_id, exc_info=True)


async def corpus_stats(session: AsyncSession, corpus_ids: list[UUID]) -> dict[UUID, CorpusStats]:
    """Owner, document and chunk counts, and last activity, for a page of corpora.

    One statement for the whole page: correlated scalar subqueries per corpus.
    `last_activity_at` is GREATEST(max query_log.created_at, max
    ingest_jobs.created_at); each max is a single backward probe of the
    (corpus_id, created_at) index on its table (migration phase10_001), so the
    cost does not grow with a corpus's history. GREATEST ignores NULLs.
    """
    if not corpus_ids:
        return {}
    from callobuzz_rag.db.models.chunk import Chunk
    from callobuzz_rag.db.models.document import Document
    from callobuzz_rag.db.models.document_version import DocumentVersion
    from callobuzz_rag.db.models.ingest_job import IngestJob
    from callobuzz_rag.db.models.query_log import QueryLog
    from callobuzz_rag.db.models.tenant import Tenant as TenantModel

    documents = (
        select(func.count(Document.id))
        .where(Document.corpus_id == Corpus.id)
        .correlate(Corpus)
        .scalar_subquery()
    )
    chunks = (
        select(func.count(Chunk.id))
        .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
        .where(Chunk.corpus_id == Corpus.id, DocumentVersion.status == "active")
        .correlate(Corpus)
        .scalar_subquery()
    )
    last_query = (
        select(func.max(QueryLog.created_at))
        .where(QueryLog.corpus_id == Corpus.id)
        .correlate(Corpus)
        .scalar_subquery()
    )
    last_job = (
        select(func.max(IngestJob.created_at))
        .where(IngestJob.corpus_id == Corpus.id)
        .correlate(Corpus)
        .scalar_subquery()
    )
    stmt = (
        select(
            Corpus.id,
            Corpus.tenant_id,
            TenantModel.name,
            documents.label("document_count"),
            chunks.label("chunk_count"),
            func.greatest(last_query, last_job).label("last_activity_at"),
        )
        .join(TenantModel, TenantModel.id == Corpus.tenant_id)
        .where(Corpus.id.in_(corpus_ids))
    )
    rows = (await session.execute(stmt)).all()
    return {
        row[0]: CorpusStats(
            tenant_id=row[1],
            tenant_name=row[2],
            document_count=row[3] or 0,
            chunk_count=row[4] or 0,
            last_activity_at=row[5],
        )
        for row in rows
    }


def _fallback_stats(corpus: Corpus) -> CorpusStats:
    """Figures for a corpus the stats query did not return (deleted meanwhile)."""
    return CorpusStats(tenant_id=corpus.tenant_id, tenant_name="")


async def _one_corpus_stats(session: AsyncSession, corpus: Corpus) -> CorpusStats:
    stats = (await corpus_stats(session, [corpus.id])).get(corpus.id)
    return stats or _fallback_stats(corpus)


def _detail_response(
    corpus: Corpus, stats: CorpusStats, migration_id: str | None = None
) -> CorpusDetailResponse:
    return CorpusDetailResponse(
        id=corpus.id,
        name=corpus.name,
        description=corpus.description,
        qdrant_collection=corpus.qdrant_collection,
        llm_config=_redact_llm_config(corpus.llm_config),
        prompt_config=corpus.prompt_config,
        retrieval_config=corpus.retrieval_config,
        embedding_config=_redact_embedding_config(corpus.embedding_config),
        applied_embedding=corpus.applied_embedding,
        migration_id=migration_id,
        created_at=corpus.created_at,
        **stats.model_dump(),
    )


def _tenant_required() -> APIError:
    return APIError(
        422, "tenant_id is required: every knowledge base belongs to a tenant", "tenant_required"
    )


def _verify_tenant_ownership(corpus: Corpus, request: Request) -> None:
    """Verify tenant owns the corpus. Raises 403 if not."""
    tenant: Tenant | None = getattr(request.state, "tenant", None)
    if tenant is not None and corpus.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Corpus does not belong to this tenant")


@router.post("", response_model=CreateCorpusResponse, status_code=201)
async def create_corpus(
    body: CreateCorpusRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _auth: None = Depends(require_admin_or_tenant),
    _owner: None = Depends(require_owner),
) -> CreateCorpusResponse:
    """Create a new corpus with dedicated Qdrant collection and API key.

    - Generates a unique API key (returned once — store securely)
    - Creates a dedicated Qdrant collection named `corpus_{id[:8]}`
    - Encrypts LLM API key if provided
    - Requires admin or tenant authentication
    - Every corpus belongs to a tenant: the admin key must send `tenant_id`
      (422 `tenant_required` otherwise); a tenant key creates for itself
    - The owning tenant's knowledge-base limit applies (its `max_corpora` override, else
      its plan's): 409 `kb_limit_reached`
    """
    # BIZ3 features
    await refuse_hidden_fields(request, body)
    # /BIZ3 features
    # Tenant scoping: enforce corpus limit
    tenant: Tenant | None = getattr(request.state, "tenant", None)
    if body.tenant_id is not None:
        if tenant is not None:
            if body.tenant_id != tenant.id:
                raise HTTPException(
                    status_code=403, detail="A tenant key can only create its own corpora"
                )
        else:
            # Admin onboarding a customer: create the corpus for that tenant.
            from callobuzz_rag.db.models.tenant import Tenant as TenantModel

            tenant = await session.get(TenantModel, body.tenant_id)
            if tenant is None:
                raise HTTPException(status_code=404, detail="Tenant not found")
    if tenant is None:
        raise _tenant_required()
    refuse_system_tenant(tenant)
    # The tenant's knowledge-base limit: its own override, else its plan's (accounts A6).
    await check_kb_room(session, tenant)

    # Prepare LLM config (encrypt API key if present)
    llm_config_dict: dict[str, Any] | None = None
    if body.llm_config is not None:
        if body.llm_config.api_key:  # P5 plans: an own key only where the plan includes it
            await refuse_key_save(session, request, tenant.id)
        llm_config_dict = body.llm_config.model_dump(exclude_none=True)
        llm_config_dict = _encrypt_llm_key(llm_config_dict, request)

    # Prepare prompt config
    prompt_config_dict: dict[str, Any] | None = None
    if body.prompt_config is not None:
        prompt_config_dict = body.prompt_config.model_dump(exclude_none=True)

    # Prepare retrieval config (expand preset + overrides)
    retrieval_config_dict: dict[str, Any] | None = None
    if body.retrieval_config is not None:
        retrieval_config_dict = _resolve_retrieval_config_input(
            body.retrieval_config, request.app.state.settings
        )

    # Embedding model: the corpus's own (validated like PUT) or the engine default.
    # `applied_embedding` records what the new, empty collection is built with, so
    # no migration is needed and the nightly drift check sees nothing to do. It
    # used to be left NULL, which the drift check read as "drifted" and queued a
    # no-op re-embed for every corpus created through the API.
    settings = request.app.state.settings
    embedding_config_dict = _validated_embedding_config(body.embedding_config, request)
    applied_embedding = {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
    }
    if embedding_config_dict and embedding_config_dict.get("model"):
        applied_embedding = {
            key: embedding_config_dict[key] for key in ("provider", "model", "dimensions")
        }

    # Create corpus record (api_key and qdrant_collection set after flush)
    corpus = Corpus(
        name=body.name,
        description=body.description,
        llm_config=llm_config_dict,
        prompt_config=prompt_config_dict,
        retrieval_config=retrieval_config_dict,
        embedding_config=embedding_config_dict,
        applied_embedding=applied_embedding,
        tenant_id=tenant.id,
    )
    session.add(corpus)
    await session.flush()  # Get the generated ID

    # The knowledge base's first key: its "Default key", with full permission (the one key
    # it always had). More named keys are made through /corpora/{id}/keys.
    # A key caller cannot mint more permission than it holds: a write key gets a write key.
    caller_key = getattr(request.state, "api_key", None)
    default_permission = caller_key.permission if isinstance(caller_key, KeyInfo) else "full"
    id_short = str(corpus.id)[:8]
    _, api_key = await create_key(
        session,
        corpus_owner(corpus),
        name=DEFAULT_KEY_NAME,
        permission=default_permission,
        request=request,
        is_default=True,
        audit=False,
    )

    # Set qdrant_collection based on ID
    collection_name = f"corpus_{id_short}"
    corpus.qdrant_collection = collection_name

    await session.commit()
    await session.refresh(corpus)

    # Create Qdrant collection
    vector_store = getattr(request.app.state, "vector_store", None)
    if vector_store is not None:
        try:
            await vector_store.create_collection(
                collection_name, int(applied_embedding["dimensions"])
            )
            logger.info(
                "Created Qdrant collection %s for corpus %s",
                collection_name,
                corpus.id,
            )
        except Exception as e:
            logger.warning(
                "Qdrant collection creation failed for corpus %s: %s (collection: %s)",
                corpus.id,
                e,
                collection_name,
            )

    await publish_initial_assistant(request, session, corpus)

    logger.info(
        "Corpus created: id=%s name=%s collection=%s",
        corpus.id,
        body.name,
        collection_name,
    )

    return CreateCorpusResponse(
        id=corpus.id,
        name=corpus.name,
        description=corpus.description,
        api_key=api_key,
        qdrant_collection=corpus.qdrant_collection,
        created_at=corpus.created_at,
    )


async def publish_initial_assistant(
    request: Request, session: AsyncSession, corpus: Corpus
) -> None:
    """Version 1 of the new knowledge base's assistant (conversation C2): the Customer support
    template, switched on unless RAG_ASSISTANT_DEFAULT_FOR_NEW_KBS=false, with leads off when
    no RAG_ENCRYPTION_KEY is set (leads are stored encrypted). Best effort: a failure is
    logged and the knowledge base still exists; the owner can publish from the dashboard."""
    store: AssistantConfigStore | None = None
    try:
        settings = request.app.state.settings
        config = default_config_for(
            corpus, encryption_available=bool(parse_key_list(settings.encryption_key))
        )
        config.enabled = bool(settings.assistant_default_for_new_kbs)
        store = AssistantConfigStore(session, request, settings)
        await store.publish_initial(corpus, config, "Customer support template")
        await session.commit()
    except Exception:
        logger.warning(
            "Could not publish the initial assistant for corpus %s", corpus.id, exc_info=True
        )
        await session.rollback()
        try:  # a rollback expires the corpus; the response below reads it
            await session.refresh(corpus)
        except Exception:
            logger.debug("Could not refresh corpus %s after a rollback", corpus.id, exc_info=True)
        return
    if store.pending_card is not None:
        try:
            await dispatch_business_card(
                session, corpus, store.pending_card, request.app.state.vector_store
            )
        except Exception:
            logger.warning("Could not queue the business card for %s", corpus.id, exc_info=True)


@router.get("", response_model=CorpusListResponse)
async def list_corpora(
    request: Request,
    tenant_id: UUID | None = Query(
        None, description="Only this tenant's corpora. A tenant key may name only itself."
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _auth: None = Depends(require_admin_or_tenant),
) -> CorpusListResponse:
    """List corpora, newest first. Admin sees all; a tenant sees only its own.

    Each item carries its tenant, document and chunk counts and last activity.
    """
    tenant: Tenant | None = getattr(request.state, "tenant", None)
    if tenant is not None:
        if tenant_id is not None and tenant_id != tenant.id:
            raise HTTPException(
                status_code=403, detail="A tenant key can list only its own corpora"
            )
        tenant_id = tenant.id

    # COB's built-in knowledge base is never listed.
    where: list[Any] = [Corpus.is_system.is_(False)]
    if tenant_id is not None:
        where.append(Corpus.tenant_id == tenant_id)
    total = (await session.execute(select(func.count(Corpus.id)).where(*where))).scalar() or 0
    result = await session.execute(
        select(Corpus)
        .where(*where)
        .order_by(Corpus.created_at.desc(), Corpus.id)
        .offset(offset)
        .limit(limit)
    )
    items = list(result.scalars().all())
    stats = await corpus_stats(session, [c.id for c in items])
    return CorpusListResponse(
        items=[
            CorpusResponse(
                id=c.id,
                name=c.name,
                description=c.description,
                qdrant_collection=c.qdrant_collection,
                created_at=c.created_at,
                **(stats.get(c.id) or _fallback_stats(c)).model_dump(),
            )
            for c in items
        ],
        count=len(items),
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{corpus_id}", response_model=CorpusDetailResponse)
async def get_corpus(
    corpus_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _auth: None = Depends(require_admin_or_tenant),
) -> CorpusDetailResponse:
    """Get corpus detail with config. LLM API key is redacted. Requires admin or tenant."""
    corpus = await session.get(Corpus, corpus_id)
    if not corpus:
        raise HTTPException(status_code=404, detail="Corpus not found")

    _verify_tenant_ownership(corpus, request)

    return _detail_response(corpus, await _one_corpus_stats(session, corpus))


@router.put("/{corpus_id}", response_model=CorpusDetailResponse)
async def update_corpus(
    corpus_id: UUID,
    body: UpdateCorpusRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _auth: None = Depends(require_admin_or_tenant),
) -> CorpusDetailResponse:
    """Update corpus name, description, LLM, prompt, retrieval or embedding config.

    Only fields present in the body change. Inside `llm_config`, `prompt_config`
    and `retrieval_config`, a field sent as null is removed (the engine default
    applies again) and `{}` changes nothing; `llm_config.api_key: null` removes
    the stored key. A whole section sent as null resets it to engine defaults.
    `description: null` clears the description. The LLM key is encrypted at
    rest and redacted in the response. Requires admin or tenant authentication;
    `llm_config`, `embedding_config` and `tenant_id` need an owner (a workspace member
    gets 403 `owner_required`).
    """
    # BIZ3 features
    await refuse_hidden_fields(request, body)
    # /BIZ3 features
    corpus = await session.get(Corpus, corpus_id)
    if not corpus:
        raise HTTPException(status_code=404, detail="Corpus not found")

    refuse_system_corpus(corpus)
    _verify_tenant_ownership(corpus, request)

    sent = body.model_fields_set
    # Members edit prompts and retrieval; the model key (BYOK) and the model that spends
    # it, the embedding (a paid re-embed) and the owning tenant are owners' (accounts A5).
    for owner_field in ("llm_config", "embedding_config", "tenant_id"):
        if owner_field in sent:
            require_owner_for(request, owner_field)
    # Whether pages are stripped of repeated furniture decides what is kept: an owner's call.
    if body.retrieval_config is not None and "boilerplate_strip" in (
        body.retrieval_config.model_fields_set
    ):
        require_owner_for(request, "boilerplate_strip")
    elif (
        "retrieval_config" in sent
        and body.retrieval_config is None
        and (corpus.retrieval_config or {}).get("boilerplate_strip") is not None
    ):
        # A whole section sent as null drops every stored setting, the owner's included.
        require_owner_for(request, "boilerplate_strip")

    if "tenant_id" in sent and body.tenant_id is None:
        raise _tenant_required()  # every corpus belongs to a tenant; there is no detach

    if body.tenant_id is not None and body.tenant_id != corpus.tenant_id:
        await _move_to_tenant(session, request, corpus, body.tenant_id)

    if body.name is not None:
        corpus.name = body.name

    if "description" in sent:
        corpus.description = body.description

    if body.llm_config is not None:
        if body.llm_config.api_key:  # P5 plans: an own key only where the plan includes it
            await refuse_key_save(session, request, corpus.tenant_id)
        corpus.llm_config = _llm_config_changes(body.llm_config, corpus.llm_config, request)
    elif "llm_config" in sent:
        corpus.llm_config = None

    if body.prompt_config is not None:
        corpus.prompt_config = _merge_config(
            corpus.prompt_config, body.prompt_config.model_dump(exclude_unset=True)
        )
    elif "prompt_config" in sent:
        corpus.prompt_config = None

    if body.retrieval_config is not None:
        corpus.retrieval_config = _retrieval_config_changes(
            body.retrieval_config, corpus.retrieval_config, request.app.state.settings
        )
    elif "retrieval_config" in sent:
        corpus.retrieval_config = None

    # --- Embedding config (Phase 8) ---
    migration_id: str | None = None
    if body.embedding_config is not None:
        embedding_config_dict = _validated_embedding_config(body.embedding_config, request)
        if embedding_config_dict:
            corpus.embedding_config = embedding_config_dict

            settings = request.app.state.settings
            from callobuzz_rag.migration.service import (
                get_non_terminal_migration,
                is_drifted,
            )

            existing_mig = await get_non_terminal_migration(session, corpus.id)
            if existing_mig and existing_mig.status in ("running", "verifying", "rolling_back"):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Corpus has an active {existing_mig.status} migration "
                        f"({existing_mig.id}). Cancel it first."
                    ),
                )

            if is_drifted(corpus, settings):
                from callobuzz_rag.migration.service import create_migration

                mig = await create_migration(session, corpus, settings)
                migration_id = str(mig.id)

    await session.commit()
    await session.refresh(corpus)

    if body.tenant_id is not None:
        from callobuzz_rag.landing.cache import invalidate_all

        invalidate_all()
    await invalidate_corpus_query_cache(request.app.state.settings, str(corpus_id))

    logger.info("Corpus updated: id=%s migration_created=%s", corpus.id, migration_id)

    return _detail_response(corpus, await _one_corpus_stats(session, corpus), migration_id)


async def _move_to_tenant(
    session: AsyncSession, request: Request, corpus: Corpus, tenant_id: UUID
) -> None:
    """Reassign a corpus to another tenant. Admin only; the target's limit applies."""
    if getattr(request.state, "tenant", None) is not None:
        raise HTTPException(status_code=403, detail="Only the admin key can move a corpus")
    from callobuzz_rag.db.models.tenant import Tenant as TenantModel

    target = await session.get(TenantModel, tenant_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    refuse_system_tenant(target)
    await check_kb_room(session, target)
    logger.info("Corpus %s moved from tenant %s to %s", corpus.id, corpus.tenant_id, tenant_id)
    corpus.tenant_id = tenant_id
    # Leads carry their tenant (its delete cascades, the inbox scopes by it): follow the move.
    await session.execute(
        update(Lead).where(Lead.corpus_id == corpus.id).values(tenant_id=tenant_id)
    )
    # LP4: the operator's website must stop using a knowledge base that is no longer theirs.
    from callobuzz_rag.db.models.landing import LandingSite

    await session.execute(
        update(LandingSite)
        .where(LandingSite.ask_corpus_id == corpus.id)
        .values(ask_corpus_id=None, ask_widget_key_id=None)
    )


@router.delete("/{corpus_id}", status_code=204)
async def delete_corpus(
    corpus_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    confirm_name: str | None = Query(
        None,
        max_length=255,
        description="The knowledge base's name, typed exactly. Required with an API key "
        "(422 confirmation_required / confirm_mismatch); a dashboard session confirms in the "
        "UI. GET .../delete-preview says what would be removed.",
    ),
    _auth: None = Depends(require_admin_or_tenant),
    _owner: None = Depends(require_owner),
) -> None:
    """Delete a corpus, its Qdrant collection, and cascade-delete all data.

    The database goes first, in one SQL statement, and the vector collection and
    stored files only after it commits (`api/services/corpus_deletion.py` says why).

    Requires admin or tenant authentication. With an API key, `confirm_name` must be the
    knowledge base's exact name.
    """
    from callobuzz_rag.api.services.corpus_deletion import (
        cleanup_corpus_storage,
        delete_corpus_rows,
    )
    from callobuzz_rag.api.services.delete_guard import require_confirm_name

    corpus = await session.get(Corpus, corpus_id)
    if not corpus:
        raise HTTPException(status_code=404, detail="Corpus not found")

    refuse_system_corpus(corpus)
    _verify_tenant_ownership(corpus, request)
    require_confirm_name(request, confirm_name, corpus.name, "knowledge base")
    stored = await delete_corpus_rows(session, [corpus_id])
    await session.commit()
    await cleanup_corpus_storage(request.app.state, stored)


@router.get("/{corpus_id}/delete-preview", response_model=DeletePreviewResponse)
async def preview_delete_corpus(
    corpus_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _auth: None = Depends(require_admin_or_tenant),
) -> DeletePreviewResponse:
    """What deleting this knowledge base would remove, and the name to confirm with.
    Changes nothing."""
    from callobuzz_rag.api.services.delete_guard import corpus_counts

    corpus = await session.get(Corpus, corpus_id)
    if not corpus:
        raise HTTPException(status_code=404, detail="Corpus not found")
    _verify_tenant_ownership(corpus, request)
    return DeletePreviewResponse(
        kind="knowledge_base",
        id=corpus.id,
        name=corpus.name,
        confirm_name=corpus.name,
        counts=await corpus_counts(session, [corpus.id]),
    )


@router.post("/{corpus_id}/rotate-key", response_model=RotateKeyResponse)
async def rotate_key(
    corpus_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _auth: None = Depends(require_admin_or_tenant),
    _owner: None = Depends(require_owner),
) -> RotateKeyResponse:
    """Replace the knowledge base's default key. The old one is immediately invalidated.

    Returns the new key (shown only once — store securely). Other named keys
    (`/corpora/{id}/keys`) are untouched; with none left, this issues a new default key.
    Requires admin or tenant authentication.
    """
    corpus = await session.get(Corpus, corpus_id)
    if not corpus:
        raise HTTPException(status_code=404, detail="Corpus not found")

    refuse_system_corpus(corpus)
    _verify_tenant_ownership(corpus, request)

    new_key = await rotate_default_key(session, corpus_owner(corpus), request=request)

    await session.commit()

    logger.info("API key rotated for corpus %s", corpus_id)

    return RotateKeyResponse(api_key=new_key)
