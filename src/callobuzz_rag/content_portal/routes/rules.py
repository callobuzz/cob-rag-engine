"""Content routing rules CRUD routes.

The corpus is in the path and authorized by `path_corpus`, the engine's shared rule:
admin any corpus, a tenant key its own corpora, a corpus key itself.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, path_corpus
from callobuzz_rag.content_portal.schemas.rules import (
    ContentRuleListResponse,
    ContentRuleResponse,
    CreateRuleRequest,
    UpdateRuleRequest,
)
from callobuzz_rag.content_portal.services.content_router import (
    ContentRouter,
    RoutingError,
)
from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/corpus", tags=["portal-rules"])


@router.get(
    "/{corpus_id}/rules",
    response_model=ContentRuleListResponse,
)
async def list_rules(
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ContentRuleListResponse:
    """List all content routing rules for a corpus (including disabled)."""
    router_svc = ContentRouter(session)
    rules = await router_svc.get_all_rules(corpus.id)
    return ContentRuleListResponse(
        items=[ContentRuleResponse.model_validate(r) for r in rules],
        count=len(rules),
    )


@router.post(
    "/{corpus_id}/rules",
    response_model=ContentRuleResponse,
    status_code=201,
)
async def create_rule(
    body: CreateRuleRequest,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ContentRuleResponse:
    """Create a new content routing rule for a corpus."""
    router_svc = ContentRouter(session)
    rule = await router_svc.create_rule(
        corpus_id=corpus.id,
        rule_type=body.rule_type,
        condition=body.condition,
        action=body.action,
        priority=body.priority,
        enabled=body.enabled,
    )
    await session.commit()
    await session.refresh(rule)
    return ContentRuleResponse.model_validate(rule)


@router.patch(
    "/{corpus_id}/rules/{rule_id}",
    response_model=ContentRuleResponse,
)
async def update_rule(
    rule_id: UUID,
    body: UpdateRuleRequest,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ContentRuleResponse:
    """Update an existing content routing rule."""
    router_svc = ContentRouter(session)

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=422,
            detail="No fields to update",
        )

    try:
        rule = await router_svc.update_rule(corpus.id, rule_id, updates)
    except RoutingError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    await session.commit()
    await session.refresh(rule)
    return ContentRuleResponse.model_validate(rule)


@router.delete(
    "/{corpus_id}/rules/{rule_id}",
    status_code=204,
)
async def delete_rule(
    rule_id: UUID,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a content routing rule."""
    router_svc = ContentRouter(session)

    try:
        await router_svc.delete_rule(corpus.id, rule_id)
    except RoutingError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    await session.commit()
