"""GET /api/v1/whoami and GET /api/v1/info — who the caller is, what the engine can do.

A dashboard (or the scraper service) holding an arbitrary key needs this to know
what to show and where the key reaches: a corpus key cannot otherwise learn its
own corpus id, which the conversation, memory, widget-key and golden routes need.
"""

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import Scope, get_session, resolve_scope
from callobuzz_rag.api.permissions import KeyInfo
from callobuzz_rag.api.schemas.identity import (
    BillingInfo,  # BIL2
    CobInfo,
    EmbeddingInfo,
    FeaturesInfo,
    InfoResponse,
    LimitsInfo,
    LLMInfo,
    LocalLLMInfo,  # PIP2
    PaymentsInfo,  # P1 payments
    RerankerInfo,
    RetrievalDefaultsInfo,
    TranscriptionInfo,
    TrustInfo,  # TV1 human
    WebSearchInfo,
    WebsiteLearningInfo,  # BIZ1
    WhatsAppInfo,  # TV6 whatsapp
    WhoAmIResponse,
    WhoAmIUser,
)
from callobuzz_rag.evaluation.optional import ragas_available
from callobuzz_rag.llm.local import local_ready  # PIP2
from callobuzz_rag.llm.thinking import clean_thinking

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["identity"])


@router.get("/whoami", response_model=WhoAmIResponse)
async def whoami(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> WhoAmIResponse:
    """Identify the credential on this request: any key, or a dashboard sign-in."""
    scope = resolve_scope(request)
    user = getattr(request.state, "user", None)
    key = getattr(request.state, "api_key", None)
    identity = {
        "auth": "session" if getattr(request.state, "auth_kind", None) == "session" else "api_key",
        "role": getattr(request.state, "role", None),
        "user": (
            WhoAmIUser(id=user.id, email=user.email, name=user.name) if user is not None else None
        ),
        "permission": key.permission if isinstance(key, KeyInfo) else None,
        "key_name": key.name if isinstance(key, KeyInfo) else None,
        "key_id": key.id if isinstance(key, KeyInfo) else None,
    }
    if scope.is_admin:
        return WhoAmIResponse(kind="admin", **identity)

    corpus = getattr(request.state, "corpus", None)
    tenant = getattr(request.state, "tenant", None)
    if tenant is None and scope.tenant_id is not None:
        from callobuzz_rag.db.models.tenant import Tenant

        tenant = await session.get(Tenant, scope.tenant_id)

    return WhoAmIResponse(
        kind="corpus" if corpus is not None else "tenant",
        tenant_id=scope.tenant_id,
        tenant_name=getattr(tenant, "name", None),
        corpus_id=getattr(corpus, "id", None),
        corpus_name=getattr(corpus, "name", None),
        **identity,
    )


async def _cob_info(settings: "Settings", session: AsyncSession) -> CobInfo:
    """COB's switches and what its knowledge base holds, for the dashboard."""
    from sqlalchemy import select

    from callobuzz_rag.cob.system import get_system_corpus
    from callobuzz_rag.db.models.cob_status import CobStatus

    corpus = await get_system_corpus(session)
    status = (await session.execute(select(CobStatus))).scalar_one_or_none()
    return CobInfo(
        enabled=bool(settings.cob_enabled),
        public=bool(settings.cob_enabled and settings.cob_public_enabled),
        corpus_id=getattr(corpus, "id", None),
        docs_version=getattr(status, "docs_version", None),
    )


def _engine_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("callobuzz-rag")
    except PackageNotFoundError:
        return "unknown"


# TV1 human
async def _trust_info(request: Request) -> TrustInfo:
    """The deployer's trust switches; unreadable settings mean the safe defaults (nothing hidden)."""
    try:
        from callobuzz_rag.accounts.settings_store import get_engine_settings

        factory: Any = getattr(request.app.state, "session_factory", None)
        engine = await get_engine_settings(factory)
        return TrustInfo(
            allow_hide_human_button=engine.trust.allow_hide_human_button,
            require_ai_disclosure=engine.trust.require_ai_disclosure,  # TV5 disclosure
            disclosure_wording_editable=engine.trust.disclosure_wording_editable,  # TV5 disclosure
        )
    except Exception:
        logger.warning("Could not read the engine's trust settings for /info", exc_info=True)
        return TrustInfo()


# /TV1 human


# TV6 whatsapp
async def _whatsapp_info(
    settings: "Settings", scope: Scope, session: AsyncSession, *, is_admin: bool = False
) -> WhatsAppInfo:
    from callobuzz_rag.channels.whatsapp.availability import (
        embedded_ready,
        engine_ready,
        plan_allows,
    )

    ready = engine_ready(settings)
    if not ready:
        return WhatsAppInfo()
    allowed = True
    if scope.tenant_id is not None:
        allowed = await plan_allows(session, scope.tenant_id)
    from callobuzz_rag.channels.whatsapp.availability import public_address_ok

    address_ok = public_address_ok(settings)
    allowed = allowed and address_ok
    return WhatsAppInfo(
        enabled=True,
        available=allowed,
        embedded_signup=allowed and embedded_ready(settings),
        public_address_missing=is_admin and not address_ok,
    )


# /TV6 whatsapp


# P1 payments
async def _payments_info(request: Request, session: AsyncSession) -> PaymentsInfo:
    """Whether online payments work here. Never fails the page: any trouble means "off"."""
    try:
        from callobuzz_rag.billing.payments.factory import provider_for

        provider = await provider_for(request.app, session)
    except Exception:
        logger.warning("Could not read the payment settings for /info", exc_info=True)
        return PaymentsInfo()
    return PaymentsInfo(enabled=provider is not None, mode=provider.mode if provider else None)


# /P1 payments


def _billing_info(request: Request) -> BillingInfo:
    return BillingInfo(enabled=bool(request.app.state.settings.billing_enabled))


@router.get("/info", response_model=InfoResponse)
async def info(
    request: Request,
    scope: Scope = Depends(resolve_scope),
    session: AsyncSession = Depends(get_session),
) -> InfoResponse:
    """What this engine offers and the limits a UI should mirror. Any valid key; no secrets.

    `limits.max_upload_bytes` and `limits.max_document_tokens` are the caller's effective
    caps: the engine's, lowered by its tenant's plan.
    """
    from callobuzz_rag.accounts.caps import effective_limits
    from callobuzz_rag.accounts.tokens import accounts_enabled
    from callobuzz_rag.api.routes.ingest import (
        ALLOWED_TYPES,
        EXTENSION_MIME_TYPES,
    )
    from callobuzz_rag.api.routes.widget_keys import engine_public_url
    from callobuzz_rag.api.schemas.ingest import MAX_RECORD_TEXT_CHARS, MAX_RECORDS_PER_REQUEST
    from callobuzz_rag.api.schemas.query import QueryRequest
    from callobuzz_rag.transcription.base import AUDIO_MIME_TYPES

    settings = request.app.state.settings
    max_upload_bytes: int = settings.max_upload_bytes
    max_document_tokens: int = settings.max_document_tokens
    if scope.tenant_id is not None:
        plan = await effective_limits(session, scope.tenant_id)
        if plan.max_upload_mb is not None:
            max_upload_bytes = min(max_upload_bytes, plan.max_upload_mb * 1024 * 1024)
        if plan.max_document_tokens is not None:
            max_document_tokens = min(max_document_tokens, plan.max_document_tokens)
    cob = await _cob_info(settings, session)
    trust = await _trust_info(request)  # TV1 human
    # LP1 landing
    from callobuzz_rag.landing.cache import landing_is_on

    landing_on = await landing_is_on(request.app.state.session_factory)
    # /LP1 landing
    transcription_on = settings.transcription_provider != "off"
    accepted = set(ALLOWED_TYPES) | (set(AUDIO_MIME_TYPES) if transcription_on else set())
    query_max = next(
        (
            m.max_length
            for m in QueryRequest.model_fields["query"].metadata
            if hasattr(m, "max_length")
        ),
        2000,
    )
    return InfoResponse(
        version=_engine_version(),
        features=FeaturesInfo(
            content_portal=bool(settings.enable_content_portal),
            accounts=accounts_enabled(settings),
            landing=landing_on,  # LP1 landing
            streaming=bool(settings.query_stream_enabled),
            transcription=TranscriptionInfo(
                enabled=transcription_on, provider=settings.transcription_provider
            ),
            reranker=RerankerInfo(
                local=getattr(request.app.state, "reranker", None) is not None,
                llm=True,
                default_provider=settings.rerank_provider,
                enabled_by_default=bool(settings.reranking_enabled),
            ),
            web_search=WebSearchInfo(
                enabled=settings.web_search_provider != "off",
                provider=settings.web_search_provider,
            ),
            cob=cob,
            # BIL2
            billing=_billing_info(request),
            eval_ragas=ragas_available(),
            # BIZ3 features
            dashboard=getattr(request.state, "dashboard_features", None),
            # /BIZ3 features
            # BIZ1 learning
            website_learning=WebsiteLearningInfo(
                enabled=bool(settings.website_learning_enabled),
                render=bool(
                    settings.website_render_url.strip()
                    and settings.website_render_token.get_secret_value()
                ),
            ),
            # /BIZ1 learning
            # PIP2 local
            local_llm=LocalLLMInfo(
                enabled=bool(settings.local_llm_enabled), ready=local_ready(settings)
            ),
            # /PIP2 local
            trust=trust,  # TV1 human
            payments=await _payments_info(request, session),  # P1 payments
            whatsapp=await _whatsapp_info(
                settings, scope, session, is_admin=bool(getattr(request.state, "is_admin", False))
            ),  # TV6 whatsapp
        ),
        embedding=EmbeddingInfo(
            provider=settings.embedding_provider,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        ),
        llm=LLMInfo(
            provider=settings.llm_provider or None,
            model=settings.llm_model,
            fast_model=settings.llm_fast_model or None,
            fast_provider_order=list(settings.llm_fast_provider_order),
            reasoning_effort=settings.llm_reasoning_effort,
            thinking=settings.llm_thinking or clean_thinking(settings.llm_reasoning_effort),
            fast_thinking=settings.llm_fast_thinking,
            language_models=dict(settings.llm_language_models),
            key_configured=bool(settings.llm_api_key.get_secret_value()),
        ),
        public_url=engine_public_url(request),
        public_url_configured=bool(settings.public_url),
        limits=LimitsInfo(
            max_upload_bytes=max_upload_bytes,
            max_document_tokens=max_document_tokens,
            allowed_mime_types=sorted(accepted),
            allowed_extensions=sorted(
                ext for ext, mime in EXTENSION_MIME_TYPES.items() if mime in accepted
            ),
            max_records_per_request=MAX_RECORDS_PER_REQUEST,
            max_record_chars=MAX_RECORD_TEXT_CHARS,
            max_query_chars=int(query_max),
        ),
        retrieval_defaults=retrieval_defaults(settings),
    )


def retrieval_defaults(settings: "Settings") -> RetrievalDefaultsInfo:
    """What each retrieval field resolves to for a corpus that stores no value.

    Asks the query pipeline's own resolver with an empty corpus config rather than
    reading `Settings` field by field, so the two can never disagree.
    """
    from callobuzz_rag.core.config_resolver import ConfigResolver

    resolved = ConfigResolver(settings).resolve_retrieval(None)
    return RetrievalDefaultsInfo(
        **{name: getattr(resolved, name) for name in RetrievalDefaultsInfo.model_fields}
    )
