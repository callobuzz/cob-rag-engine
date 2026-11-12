"""A knowledge base's own webhooks: where its leads and handoffs go (conversation C4).

Owners manage them; they may only carry `lead.created` and `handoff.requested`, which
engine-wide webhooks can no longer receive (contact data goes only to the business that owns
it). The address must be https on the public internet: the worker posts to it, so a tenant
must not be able to point it inside the operator's network.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.deps import get_session, path_corpus, require_owner
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.leads import (
    CorpusWebhookCreateRequest,
    CorpusWebhookListResponse,
    CorpusWebhookResponse,
    CorpusWebhookUpdateRequest,
)
from callobuzz_rag.core.url_guard import UnsafeURLError, acheck_public_https_url
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.webhook_config import WebhookConfig

router = APIRouter(prefix="/api/v1/corpora/{corpus_id}/webhooks", tags=["webhooks"])


def _out(w: WebhookConfig) -> CorpusWebhookResponse:
    assert w.corpus_id is not None
    return CorpusWebhookResponse(
        id=w.id,
        corpus_id=w.corpus_id,
        url=w.url,
        events=list(w.events),
        enabled=w.enabled,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )


async def _own(session: AsyncSession, corpus: Corpus, webhook_id: UUID) -> WebhookConfig:
    hook = await session.get(WebhookConfig, webhook_id)
    if hook is None or hook.corpus_id != corpus.id:
        raise APIError(404, "Webhook not found", "webhook_not_found")
    return hook


@router.get("", response_model=CorpusWebhookListResponse)
async def list_corpus_webhooks(
    corpus: Corpus = Depends(path_corpus), session: AsyncSession = Depends(get_session)
) -> CorpusWebhookListResponse:
    """The webhooks of this knowledge base (secrets are never returned)."""
    rows = await session.execute(
        select(WebhookConfig)
        .where(WebhookConfig.corpus_id == corpus.id)
        .order_by(WebhookConfig.created_at.asc())
    )
    hooks = [_out(w) for w in rows.scalars().all()]
    return CorpusWebhookListResponse(webhooks=hooks, count=len(hooks))


@router.post("", response_model=CorpusWebhookResponse, status_code=201)
async def create_corpus_webhook(
    body: CorpusWebhookCreateRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> CorpusWebhookResponse:
    """Send this knowledge base's new leads (and handoff requests) to an https address."""
    try:
        await acheck_public_https_url(str(body.url))
    except UnsafeURLError:
        raise APIError(
            422,
            "That address cannot be used: it must be an https address on the public internet.",
            "webhook_url_not_allowed",
        ) from None
    hook = WebhookConfig(
        url=str(body.url),
        events=sorted(set(body.events)),
        secret=body.secret,
        enabled=True,
        corpus_id=corpus.id,
    )
    session.add(hook)
    await session.flush()
    await record_audit(
        session,
        request,
        "corpus_webhook.created",
        tenant_id=corpus.tenant_id,
        target_type="webhook",
        target_id=str(hook.id),
        details={
            "corpus_id": str(corpus.id),
            "webhook_id": str(hook.id),
            "events": list(hook.events),
        },
    )
    await session.commit()
    await session.refresh(hook)
    return _out(hook)


@router.patch("/{webhook_id}", response_model=CorpusWebhookResponse)
async def update_corpus_webhook(
    webhook_id: UUID,
    body: CorpusWebhookUpdateRequest,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> CorpusWebhookResponse:
    """Pause or resume a webhook without deleting it (and its secret)."""
    hook = await _own(session, corpus, webhook_id)
    hook.enabled = body.enabled
    await session.commit()
    await session.refresh(hook)
    return _out(hook)


@router.delete("/{webhook_id}", status_code=204)
async def delete_corpus_webhook(
    webhook_id: UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> None:
    hook = await _own(session, corpus, webhook_id)
    await record_audit(
        session,
        request,
        "corpus_webhook.deleted",
        tenant_id=corpus.tenant_id,
        target_type="webhook",
        target_id=str(hook.id),
        details={
            "corpus_id": str(corpus.id),
            "webhook_id": str(hook.id),
            "events": list(hook.events),
        },
    )
    await session.delete(hook)
    await session.commit()
