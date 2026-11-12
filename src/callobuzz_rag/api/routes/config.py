"""Self-service configuration endpoints: a corpus's prompt and retrieval settings.

A corpus key reads and changes its own corpus. An admin or tenant key names the
corpus with `?corpus_id=` and gets exactly the same view and the same bounds
(see `api.deps.scoped_corpus`); a tenant can reach only its own corpora.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, require_owner_for, scoped_corpus
from callobuzz_rag.api.features import refuse_hidden_fields  # BIZ3 features
from callobuzz_rag.api.routes.corpus import (
    _merge_config,
    _redact_llm_config,
    _retrieval_config_changes,
    invalidate_corpus_query_cache,
)
from callobuzz_rag.api.schemas.config_self_service import (
    TenantConfigResponse,
    UpdateTenantConfigRequest,
)
from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("", response_model=TenantConfigResponse)
async def get_my_config(
    corpus: Corpus = Depends(scoped_corpus),
) -> TenantConfigResponse:
    """Get the current configuration of the corpus (the key's own, or `?corpus_id=`)."""
    return TenantConfigResponse(
        corpus_id=corpus.id,
        retrieval_config=corpus.retrieval_config,
        prompt_config=corpus.prompt_config,
        llm_config=_redact_llm_config(corpus.llm_config),
    )


@router.patch("", response_model=TenantConfigResponse)
async def update_my_config(
    body: UpdateTenantConfigRequest,
    request: Request,
    corpus: Corpus = Depends(scoped_corpus),
    session: AsyncSession = Depends(get_session),
) -> TenantConfigResponse:
    """Update the corpus's retrieval settings and prompts (the key's own, or `?corpus_id=`).

    Every key kind gets the same bounds as `PUT /corpora/{id}`. A field sent as null is removed (engine default again),
    `{}` changes nothing, and a whole section sent as null resets it.
    LLM model/key changes require admin API (PUT /corpora/{id}).
    """
    # BIZ3 features
    await refuse_hidden_fields(request, body)
    # /BIZ3 features
    # With a corpus key, APIKeyAuthMiddleware loads the corpus in its own short-lived
    # session and expunges it, so `corpus` is DETACHED. Mutating it wrote nothing and `session.refresh()`
    # then raised `InvalidRequestError: Instance '<Corpus>' is not persistent within
    # this Session` — a 500 on every call. Re-load it into the request's session and
    # mutate that instead.
    managed = await session.get(Corpus, corpus.id)
    if managed is None:
        raise HTTPException(status_code=404, detail="Corpus not found")

    sent = body.model_fields_set

    # Whether pages are stripped of repeated furniture decides what is kept: an owner's call.
    if body.retrieval_config is not None and "boilerplate_strip" in (
        body.retrieval_config.model_fields_set
    ):
        require_owner_for(request, "boilerplate_strip")
    elif (
        "retrieval_config" in sent
        and body.retrieval_config is None
        and (managed.retrieval_config or {}).get("boilerplate_strip") is not None
    ):
        # A whole section sent as null drops every stored setting, the owner's included.
        require_owner_for(request, "boilerplate_strip")

    if body.retrieval_config is not None:
        managed.retrieval_config = _retrieval_config_changes(
            body.retrieval_config, managed.retrieval_config, request.app.state.settings
        )
    elif "retrieval_config" in sent:
        managed.retrieval_config = None

    if body.prompt_config is not None:
        managed.prompt_config = _merge_config(
            managed.prompt_config, body.prompt_config.model_dump(exclude_unset=True)
        )
    elif "prompt_config" in sent:
        managed.prompt_config = None

    await session.commit()
    await session.refresh(managed)

    # Invalidate query cache
    await invalidate_corpus_query_cache(request.app.state.settings, str(managed.id))

    logger.info("Tenant config updated for corpus %s", managed.id)

    return TenantConfigResponse(
        corpus_id=managed.id,
        retrieval_config=managed.retrieval_config,
        prompt_config=managed.prompt_config,
        llm_config=_redact_llm_config(managed.llm_config),
    )
