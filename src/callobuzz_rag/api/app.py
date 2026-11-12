"""FastAPI application factory."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from callobuzz_rag.api.routes import (
    admin,
    analytics,
    api_keys,
    articles,  # BIZ3
    assistant,
    assistant_analytics,
    assistant_review,
    assistant_setup,
    audit,
    auth,
    avatars,  # BIZ4
    billing,  # BIL2
    billing_addons,  # BIL5
    billing_admin,  # BIL2
    billing_ledger,  # BIL3
    billing_plans,  # P6 plan choice
    billing_shop,  # P5 plans
    billing_statements,  # BIL6
    cob,
    cob_admin,
    cob_public,  # COB6
    config,
    conversations,
    corpus,
    corpus_webhooks,
    costs,
    docs,
    documents,
    email_admin,
    engine_settings,
    evals,
    fair_use,  # PIP3
    feedback,
    gaps,
    golden,
    handoffs,
    health,
    home,  # BIZ3
    identity,
    ingest,
    invoices,  # P2 invoices
    landing,  # LP1 landing
    landing_admin,  # LP1 landing
    landing_public,  # LP3 landing
    leads,
    legal,  # P3 legal
    legal_admin,  # P3 legal
    legal_public,  # P3 legal
    local_llm,  # PIP2
    members,
    memories,
    migrations,
    models,
    onboarding,  # BIZ4
    pipelines,  # PIP1
    plans,
    price_sheet,  # TV3
    provider_balances,  # BIL1
    query,
    service_orders,  # P5 plans
    support,  # TV3
    support_admin,  # TV3
    tenants,
    usage,
    value,  # TV4
    website,  # BIZ1
    website_public,  # BIZ2
    whatsapp,  # TV6
    whatsapp_public,  # TV6
    widget,
    widget_keys,
)

# P1 payments
from callobuzz_rag.api.routes import company as company_routes
from callobuzz_rag.api.routes import demo as demo_routes  # L5a demo
from callobuzz_rag.api.routes import payments as payments_routes
from callobuzz_rag.api.routes import payments_admin as payments_admin_routes
from callobuzz_rag.api.routes import payments_public as payments_public_routes

# /P1 payments
from callobuzz_rag.api.routes import webhooks as webhooks_routes
from callobuzz_rag.core.config import get_settings
from callobuzz_rag.core.logging import configure_logging
from callobuzz_rag.db.engine import create_engine, create_session_factory
from callobuzz_rag.parsing.ocr_langs import warn_ocr_config

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.storage.base import ObjectStorageProvider

logger = logging.getLogger(__name__)


async def _object_storage(settings: "Settings") -> "ObjectStorageProvider | None":
    """The engine's object storage, after checking the bucket is reachable.

    ensure_bucket existed and had no caller, so a wrong endpoint, a wrong key or a
    missing bucket was first discovered by a user uploading a document — possibly
    days after the deploy that broke it. Checking here surfaces it in the startup
    log instead. Deliberately not fatal: a transient S3 blip at boot must not take
    the API down when `restart: unless-stopped` would just loop it, and everything
    except ingestion works without object storage. The provider is returned even
    when the check fails (the endpoint may come back); None only when it cannot be
    built at all.
    """
    from callobuzz_rag.storage.factory import object_storage, storage_reachable

    # L2 storage
    where = (
        settings.storage_local_root
        if settings.storage_provider == "local"
        else settings.s3_endpoint
    )
    try:
        storage = object_storage(settings)
    except Exception as e:
        logger.error(
            "Object storage could not be configured (RAG_STORAGE_PROVIDER=%s, %s): %s",
            settings.storage_provider,
            where,
            e,
        )
        return None
    try:
        if settings.s3_auto_create_bucket or settings.storage_provider == "local":
            await storage.ensure_bucket(settings.s3_bucket)
        if await storage_reachable(storage, settings.s3_bucket):
            logger.info("Object storage bucket %s reachable", settings.s3_bucket)
        elif settings.storage_provider == "local":
            logger.error(
                "File storage folder %r is NOT writable. Document ingestion will fail. "
                "Check RAG_STORAGE_LOCAL_ROOT and the folder's permissions.",
                where,
            )
        else:
            logger.error(
                "Object storage bucket %r is NOT reachable at %s. Document ingestion will "
                "fail. Check RAG_S3_ENDPOINT / RAG_S3_ACCESS_KEY / RAG_S3_SECRET_KEY / "
                "RAG_S3_BUCKET, or set RAG_S3_AUTO_CREATE_BUCKET=true if the credential is "
                "allowed to create it.",
                settings.s3_bucket,
                where,
            )
    except Exception as e:
        logger.error("Object storage check failed (%s): %s", where, e)
    # /L2 storage
    return storage


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown lifecycle."""
    settings = get_settings()
    configure_logging(settings.log_level)
    warn_ocr_config(settings.ocr_engine, settings.ocr_langs)

    # --- Encryption key ---
    # Checked FIRST, before any engine, pool or tracing client is built: a
    # misconfiguration that must abort the process should abort it before the
    # process has acquired anything to clean up.
    # Auto-generating a throwaway key is a DEV convenience and a production
    # data-loss bug: a new key on every restart silently orphans whatever the
    # previous run encrypted, and the only signal was a warning in a log nobody
    # tails. Outside debug, refuse to start instead. A container that will not
    # boot is a far better outcome than one that boots and quietly makes its
    # own data unreadable.
    if not settings.encryption_key:
        if not settings.debug:
            raise RuntimeError(
                "RAG_ENCRYPTION_KEY is not set. It encrypts per-corpus LLM API keys at rest; "
                "auto-generating one would produce a different key on every restart and orphan "
                "everything the previous run encrypted. Generate one with:\n"
                '  python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"\n'
                "then set RAG_ENCRYPTION_KEY and keep a copy somewhere you can read it back — "
                "GitHub Secrets is write-only. To rotate later, put the new key FIRST in a "
                "comma-separated list and keep the old one until the re-encrypt pass has run."
            )
        from callobuzz_rag.core.secrets import generate_fernet_key

        settings.encryption_key = generate_fernet_key()
        logger.warning(
            "RAG_ENCRYPTION_KEY not set — auto-generated for this process only (RAG_DEBUG=true). "
            "Anything encrypted now becomes unreadable on the next restart."
        )
    else:
        # Validates every key up front rather than at first use, and surfaces
        # an in-progress rotation in the startup log.
        from callobuzz_rag.core.secrets import FernetEncryptor

        key_count = FernetEncryptor(settings.encryption_key).key_count
        if key_count > 1:
            logger.info(
                "RAG_ENCRYPTION_KEY holds %d keys — key rotation in progress. New writes use the "
                "first key; run the re-encrypt pass, then drop the trailing keys.",
                key_count,
            )

    # --- E-mail provider (accounts A2) ---
    # Refuses to start on the log provider in production (it would write codes to the
    # log) and on SMTP without a host or sender. The Celery worker makes the same check
    # when it imports its e-mail task.
    from callobuzz_rag.accounts.email.factory import email_provider_from_settings

    app.state.email_provider_name = email_provider_from_settings(settings).name
    logger.info("E-mail provider: %s", app.state.email_provider_name)

    # --- Accounts (A3) ---
    # Without RAG_SESSION_SECRET the account routes answer 503 and Bearer tokens are
    # refused; API keys keep working, so an upgraded deployment is not taken down.
    from callobuzz_rag.accounts.tokens import accounts_enabled, configured_secrets

    if not configured_secrets(settings):
        if accounts_enabled(settings):
            logger.warning(
                "RAG_SESSION_SECRET not set: dashboard sign-in uses a random secret for this "
                "process only (RAG_DEBUG=true)."
            )
        else:
            logger.warning(
                "RAG_SESSION_SECRET not set: dashboard sign-in is OFF (account routes answer "
                "503; API keys are unaffected). Run `make setup` (or scripts/generate_secrets.py) to set it."
            )

    engine = create_engine(settings)
    app.state.session_factory = create_session_factory(engine)
    app.state.settings = settings

    # Decrypts corpus-stored LLM keys at query time (ConfigResolver). Without it a
    # corpus's own key was ignored and every query ran on the engine default key.
    from callobuzz_rag.core.secrets import encryptor_from_settings

    app.state.encryptor = encryptor_from_settings(settings)

    # --- Phase 3: Langfuse tracing ---
    from callobuzz_rag.core.tracing import init_langfuse

    init_langfuse(settings)

    # --- Object storage ---
    # Kept on app.state so deleting a corpus can remove its stored files. It was
    # never set, so `delete_corpus` always skipped that cleanup and a deleted
    # corpus's uploads stayed in the bucket.
    app.state.object_store = await _object_storage(settings)

    # --- Phase 4: Qdrant vector store + CollectionManager ---
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

    vector_store = QdrantVectorProvider(settings)

    from callobuzz_rag.vectordb.collection_manager import CollectionManager

    collection_manager = CollectionManager(
        vector_store=vector_store,
        embedding_dimensions=settings.embedding_dimensions,
    )
    app.state.collection_manager = collection_manager

    # Self-healing: ensure all existing corpora have Qdrant collections
    try:
        from sqlalchemy import select

        from callobuzz_rag.db.models.corpus import Corpus

        async with create_session_factory(engine)() as session:
            result = await session.execute(select(Corpus.qdrant_collection))
            corpus_collections = result.scalars().all()

        for coll_name in corpus_collections:
            try:
                await collection_manager.ensure_exists(coll_name)
            except Exception as e:
                logger.warning(
                    "Self-healing: failed to ensure collection %s: %s",
                    coll_name,
                    e,
                )

        if corpus_collections:
            logger.info(
                "Self-healing: verified %d corpus collection(s)",
                len(corpus_collections),
            )
    except Exception as e:
        logger.warning(
            "Collection self-healing failed (DB or Qdrant unavailable): %s",
            e,
        )

    # --- COB1: the built-in assistant's workspace and docs (never blocks start-up) ---
    try:
        from callobuzz_rag.cob.kb import load_docs_index
        from callobuzz_rag.cob.sync import maybe_enqueue_sync
        from callobuzz_rag.cob.system import ensure_cob_system

        cob_session_factory = create_session_factory(engine)
        async with cob_session_factory() as session:
            cob = await ensure_cob_system(session, settings=settings, vector_store=vector_store)
            await session.commit()
        app.state.cob_corpus_id = cob.corpus_id

        def _enqueue_cob_sync(version: str) -> None:
            from callobuzz_rag.worker.cob_tasks import cob_sync_task

            cob_sync_task.delay()
            logger.info("COB docs sync queued for version %s", version)

        from callobuzz_rag.cob.system import engine_embedding, get_system_corpus

        async with cob_session_factory() as session:
            system_corpus = await get_system_corpus(session)
            embedding_changed = system_corpus is not None and (
                system_corpus.applied_embedding or {}
            ) != engine_embedding(settings)
        await maybe_enqueue_sync(
            cob_session_factory,
            load_docs_index(),
            delay=_enqueue_cob_sync,
            now=datetime.now(UTC),
            embedding_changed=embedding_changed,
        )
    except Exception as e:
        logger.warning("COB docs setup skipped (database, queue or docs unavailable): %s", e)

    # --- Phase 2+3: Query pipeline providers (config-driven embedding) ---
    # Non-fatal: if providers fail to init, ingestion still works; query API returns 503
    try:
        from callobuzz_rag.embeddings.factory import create_embedding_provider
        from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
        from callobuzz_rag.retrieval.context_assembly import ContextAssembler
        from callobuzz_rag.retrieval.safety import ContentSanitizer

        embedder = create_embedding_provider(settings)
        llm_provider = LiteLLMProvider(settings)
        sanitizer = ContentSanitizer(settings)
        context_assembler = ContextAssembler(settings, sanitizer=sanitizer)

        app.state.embedder = embedder
        app.state.vector_store = vector_store
        app.state.llm_provider = llm_provider
        app.state.context_assembler = context_assembler
    except Exception as e:
        logger.warning("Phase 2 provider init failed (query API unavailable): %s", e)
        app.state.embedder = None
        app.state.vector_store = vector_store
        app.state.llm_provider = None
        app.state.context_assembler = None

    # Reranker is optional — init independently so its failure doesn't kill query API
    app.state.reranker = None
    try:
        from callobuzz_rag.retrieval.reranking.bge import BGERerankProvider

        app.state.reranker = BGERerankProvider(
            model_name=settings.rerank_model,
            max_length=settings.rerank_max_length,
            batch_size=settings.rerank_batch_size,
            fp16=settings.rerank_fp16,
        )
    except Exception as e:
        logger.warning("Reranker init failed (will skip reranking): %s", e)

    # Load the cross-encoder now, in the background, so the first real query does
    # not pay for it. `BGERerankProvider` loads lazily to keep startup fast, which
    # is right — but it charges the cost to whoever queries first. This happens
    # ONCE per container start; every query afterwards short-circuits in
    # `_ensure_model`. Measured 2026-08-14, a warm 20-candidate rerank is 406ms on
    # GPU and 9.7s on CPU (a real 20-candidate query), which supersedes the "16.2s CPU /
    # 18.4s GPU, 0.59s / 0.10s warm" that used to be quoted here. Those warm
    # figures were for THREE candidates, and a real query reranks 20-40.
    # A task rather than an
    # await: blocking here would push the container past its healthcheck window.
    # The reference is held on app.state because a bare `create_task` may be
    # garbage-collected mid-flight.
    app.state.reranker_warmup_task = None
    # Is there a GPU? Asked once, here and off the event loop (importing torch takes
    # seconds), so the first preset request does not stall every other request.
    try:
        from callobuzz_rag.core.retrieval_presets import probe_gpu

        app.state.gpu_available = await probe_gpu()
    except Exception as e:
        app.state.gpu_available = False
        logger.warning("GPU probe failed (presets will leave re-ranking off): %s", e)
    # Warm only when something can reach the cross-encoder: re-ranking on for the whole
    # engine, or any knowledge base that enabled the built-in one (decided in the task, so
    # the database check never delays startup). See retrieval/reranking/warmup.py.
    if app.state.reranker is not None:
        from callobuzz_rag.retrieval.reranking.warmup import warm_reranker

        app.state.reranker_warmup_task = asyncio.create_task(
            warm_reranker(app.state.reranker, settings, app.state.session_factory)
        )

    # --- Phase 3: Webhook dispatcher (Epic 3-14) ---
    app.state.webhook_dispatcher = None
    try:
        from callobuzz_rag.core.webhooks import WebhookDispatcher

        app.state.webhook_dispatcher = WebhookDispatcher(
            settings, session_factory=app.state.session_factory
        )
        logger.info("Webhook dispatcher initialized")
    except Exception as e:
        logger.warning("Webhook dispatcher init failed (webhooks unavailable): %s", e)

    # Seed config table with defaults (idempotent)
    from sqlalchemy import select

    from callobuzz_rag.db.models.config import Config

    try:
        async with create_session_factory(engine)() as session:
            existing = await session.execute(select(Config).limit(1))
            if not existing.scalar_one_or_none():
                session.add_all(
                    [
                        Config(key="chunk_schema_version", value={"version": "1.0"}),
                        Config(key="parser_version", value={"version": "docling"}),
                        Config(
                            key="embedding_model",
                            value={
                                "model": settings.embedding_model,
                                "dimensions": settings.embedding_dimensions,
                                "provider": settings.embedding_provider,
                            },
                        ),
                    ]
                )
                await session.commit()
                logger.info("Config table seeded with defaults")
    except Exception as e:
        logger.warning("Config table seeding failed (DB may be unavailable): %s", e)

    # --- Phase 3: Store initial settings hash for config-change detection (Epic 3-11) ---
    try:
        from callobuzz_rag.evaluation.scheduler import (
            CONFIG_KEY_SETTINGS_HASH,
            settings_hash,
        )

        async with create_session_factory(engine)() as session:
            result = await session.execute(
                select(Config).where(Config.key == CONFIG_KEY_SETTINGS_HASH)
            )
            if not result.scalar_one_or_none():
                initial_hash = settings_hash(settings)
                session.add(
                    Config(
                        key=CONFIG_KEY_SETTINGS_HASH,
                        value={"hash": initial_hash},
                    )
                )
                await session.commit()
                logger.info("Stored initial settings hash: %s", initial_hash[:12])
    except Exception as e:
        logger.warning("Settings hash init failed (eval scheduling unavailable): %s", e)

    # PIP2 local
    from callobuzz_rag.llm.local import start_refresher, stop_refresher

    start_refresher(app)
    # /PIP2 local

    # L5a demo: the nightly reset and the first seed (demo mode only)
    if settings.demo_mode:
        from callobuzz_rag.demo import scheduler as demo_scheduler

        demo_scheduler.start_scheduler(app)
    # /L5a demo

    yield

    # Shutdown: close providers with open connections
    # PIP2 local
    await stop_refresher(app)
    # /PIP2 local
    # L5a demo
    if settings.demo_mode:
        from callobuzz_rag.demo import scheduler as demo_scheduler

        await demo_scheduler.stop_scheduler(app)
        demo_redis = getattr(app.state, "demo_redis", None)
        if demo_redis is not None:
            await demo_redis.aclose()
    # /L5a demo
    if getattr(app.state, "embedder", None) is not None:
        await app.state.embedder.close()
    await vector_store.close()
    if getattr(app.state, "widget_rate_limiter", None) is not None:
        await app.state.widget_rate_limiter.close()
    if getattr(app.state, "account_rate_limiter", None) is not None:
        await app.state.account_rate_limiter.close()
    if getattr(app.state, "cob_public_limiter", None) is not None:  # COB6
        await app.state.cob_public_limiter.aclose()

    # Shutdown Langfuse (flush pending events)
    from callobuzz_rag.core.tracing import shutdown_langfuse

    shutdown_langfuse()

    await engine.dispose()


def _openapi_with_security(app: FastAPI) -> dict[str, Any]:
    """The generated spec plus the engine's credentials, declared per operation.

    Generated clients and the /docs "Authorize" button then know which header
    each route takes; public routes are marked as needing none. Every keyed route
    also takes a dashboard session (`BearerAuth`); the signed-in `/api/v1/auth/*`
    routes take only a session.
    """
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    from callobuzz_rag.api.auth import (
        OPEN_PREFIXES,
        OPTIONAL_AUTH_PATHS,
        PUBLIC_PATHS,
        WIDGET_PREFIXES,
    )

    schema = get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes
    )
    schema.setdefault("components", {})["securitySchemes"] = {
        "ApiKey": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "Admin key (RAG_ADMIN_API_KEY), tenant key (tk_...) or corpus key (ck_...).",
        },
        "WidgetKey": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Widget-Key",
            "description": "Publishable widget key (pk_...); widget routes only.",
        },
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Dashboard sign-in access token (POST /api/v1/auth/login).",
        },
    }
    for path, operations in schema.get("paths", {}).items():
        if path in OPTIONAL_AUTH_PATHS:
            security: list[dict[str, list[str]]] = [{}, {"BearerAuth": []}]  # optional
        elif path.startswith(WIDGET_PREFIXES):
            security = [{"WidgetKey": []}]
        elif path.startswith(OPEN_PREFIXES) or path in PUBLIC_PATHS:
            security = []
        elif path.startswith(("/api/v1/auth/", "/api/v1/docs/", "/api/v1/cob/")):
            security = [{"BearerAuth": []}]
        else:
            security = [{"ApiKey": []}, {"BearerAuth": []}]
        for operation in operations.values():
            if isinstance(operation, dict):
                operation["security"] = security
    app.openapi_schema = schema
    return schema


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    # Which e-mail addresses request bodies accept: reserved names (.local, .test, ...)
    # only in development. See accounts/email_rules.py.
    from callobuzz_rag.accounts import email_rules

    email_rules.configure(is_prod=settings.is_prod)

    # Interactive docs are a complete, self-service map of every route and
    # schema, served without an API key (they are in auth's PUBLIC_PATHS). That
    # is the right trade in dev and the wrong one on a deployed box, so prod
    # turns all three off at the source rather than relying on nothing being
    # able to reach them.
    docs_kwargs: dict[str, str | None] = (
        {"docs_url": None, "redoc_url": None, "openapi_url": None} if settings.is_prod else {}
    )

    app = FastAPI(
        title="cob-rag-engine",
        version="0.2.0",
        description=(
            "Self-hosted, multi-tenant knowledge layer for AI assistants and agents. "
            "Authenticate with X-API-Key: the admin key, a tenant key (tk_...) or a "
            "corpus key (ck_...), or, for dashboard users, Authorization: Bearer <access "
            "token> from POST /api/v1/auth/login. Widget routes take a publishable "
            "X-Widget-Key (pk_...); /api/v1/public/ routes take no credential."
        ),
        lifespan=lifespan,
        **docs_kwargs,  # type: ignore[arg-type]
    )
    app.openapi = lambda: _openapi_with_security(app)  # type: ignore[method-assign]

    from callobuzz_rag.api.errors import register_error_handlers

    register_error_handlers(app)

    # CORS. The engine is a server-to-server service: its callers are backend
    # services of the integrating application, not browsers, so a deployed
    # engine needs no allowed origin at all. `["*"]` is the dev default and
    # would let any page on the internet script the engine from a visitor's
    # browser, so prod refuses it rather than inheriting it by omission.
    cors_origins = settings.cors_origins
    if settings.is_prod and cors_origins == ["*"]:
        logger.warning(
            "RAG_CORS_ORIGINS is '*' in a production environment — refusing it "
            "and allowing no browser origin. Set an explicit list if a browser "
            "genuinely needs to call the engine directly.",
        )
        cors_origins = []

    # Langfuse middleware (per-request trace creation)
    from callobuzz_rag.core.tracing import LangfuseMiddleware

    app.add_middleware(LangfuseMiddleware)

    # API Key authentication middleware (Phase 4)
    from callobuzz_rag.api.auth import APIKeyAuthMiddleware

    # L5a demo: inside the auth layer (runs after authentication), installed in demo mode only
    if settings.demo_mode:
        from callobuzz_rag.demo.guard import DemoGuardMiddleware

        app.add_middleware(DemoGuardMiddleware, admin_key=settings.admin_api_key.get_secret_value())
    # /L5a demo
    app.add_middleware(APIKeyAuthMiddleware)

    # CORS wraps the auth layer (Starlette runs the last-added middleware
    # outermost), so a 401 for a wrong key still carries the CORS headers and a
    # browser can tell "wrong key" from "network error".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        # A browser on another origin (the dashboard) reads this to say "try again in N minutes".
        expose_headers=["Retry-After"],
    )

    # Widget CORS goes on LAST so it is the outermost layer: it has to answer the
    # browser's preflight for customer sites before the global CORS list, which
    # cannot know them, rejects it. See widget/security.py.
    from callobuzz_rag.widget.security import WidgetCORSMiddleware

    app.add_middleware(WidgetCORSMiddleware)

    # Include routers
    app.include_router(health.router)
    app.include_router(identity.router)
    app.include_router(auth.router)
    app.include_router(corpus.router)
    app.include_router(ingest.router)
    app.include_router(query.router)
    app.include_router(feedback.router)
    app.include_router(webhooks_routes.router)
    app.include_router(corpus_webhooks.router)
    app.include_router(analytics.router)
    app.include_router(gaps.router)
    app.include_router(golden.router)
    app.include_router(admin.router)
    app.include_router(tenants.router)
    app.include_router(tenants.operator_router)
    app.include_router(plans.admin_router)
    app.include_router(pipelines.router)  # PIP1
    app.include_router(local_llm.router)  # PIP2
    app.include_router(fair_use.admin_router)  # PIP3
    app.include_router(fair_use.owner_router)  # PIP3
    app.include_router(audit.router)
    app.include_router(tenants.self_router)
    app.include_router(members.me_router)
    app.include_router(members.admin_router)
    app.include_router(members.public_router)
    app.include_router(config.router)
    app.include_router(models.router)
    app.include_router(documents.router)
    app.include_router(conversations.router)
    app.include_router(assistant.router)
    app.include_router(assistant.templates_router)
    app.include_router(assistant_setup.router)
    app.include_router(evals.router)
    app.include_router(assistant_review.router)
    app.include_router(assistant_analytics.corpus_router)
    app.include_router(assistant_analytics.scope_router)
    app.include_router(memories.router)
    app.include_router(migrations.router)
    app.include_router(widget_keys.router)
    for key_router in api_keys.ROUTERS:
        app.include_router(key_router)
    app.include_router(usage.router)
    app.include_router(costs.router)
    app.include_router(provider_balances.router)  # BIL1
    app.include_router(billing_admin.router)  # BIL2
    app.include_router(billing_ledger.router)  # BIL3
    app.include_router(billing_addons.router)  # BIL5
    app.include_router(billing_statements.admin_router)  # BIL6
    app.include_router(billing_statements.router)  # BIL6
    app.include_router(billing.router)  # BIL2
    # P1 payments
    app.include_router(company_routes.router)
    app.include_router(payments_admin_routes.router)
    app.include_router(payments_routes.router)
    app.include_router(payments_public_routes.router)
    # /P1 payments
    # P2 invoices
    app.include_router(invoices.admin_router)
    app.include_router(invoices.router)
    # /P2 invoices
    # P5 plans
    app.include_router(service_orders.admin_router)
    app.include_router(service_orders.router)
    app.include_router(billing_shop.router)
    # /P5 plans
    app.include_router(billing_plans.router)  # P6 plan choice
    app.include_router(billing_plans.public_router)  # P6 plan choice
    app.include_router(cob_admin.router)  # COB1
    app.include_router(cob.router)  # COB2
    app.include_router(cob_public.router)  # COB6
    app.include_router(docs.router)  # COB8
    app.include_router(engine_settings.admin_router)
    app.include_router(engine_settings.public_router)
    app.include_router(email_admin.router)
    app.include_router(leads.router)
    app.include_router(leads.widget_router)
    app.include_router(leads.public_router)
    app.include_router(handoffs.router)
    app.include_router(widget.router)
    # BIZ3 simple mode
    app.include_router(articles.router)
    app.include_router(home.router)
    # /BIZ3 simple mode
    # BIZ1 learning
    app.include_router(website.router)
    # /BIZ1 learning
    # BIZ2 freshness
    app.include_router(website_public.router)
    app.include_router(website_public.widget_router)  # BIZ2
    # /BIZ2 freshness
    # BIZ4 avatars
    app.include_router(avatars.router)
    app.include_router(onboarding.router)
    # /BIZ4 avatars
    # TV4 value
    app.include_router(value.router)
    app.include_router(value.admin_router)
    # /TV4 value
    # TV3 support
    app.include_router(support.router)
    app.include_router(support_admin.router)
    # /TV3 support
    # TV3 price sheet
    app.include_router(price_sheet.admin_router)
    app.include_router(price_sheet.public_router)
    # /TV3 price sheet
    # P3 legal
    app.include_router(legal_admin.router)
    app.include_router(legal_public.router)
    app.include_router(legal.router)
    # /P3 legal
    # LP1 landing
    app.include_router(landing_admin.router)
    app.include_router(landing.router)
    app.include_router(landing_public.router)  # LP3 landing
    # /LP1 landing
    app.include_router(whatsapp.router)  # TV6
    app.include_router(whatsapp.admin_router)  # TV6
    app.include_router(whatsapp_public.router)  # TV6
    app.include_router(demo_routes.router)  # L5a demo: 404 demo_mode_off when demo mode is off

    # --- Content Portal (Phase 7: conditional registration) ---
    if settings.enable_content_portal:
        from callobuzz_rag.content_portal import get_portal_router

        portal_router = get_portal_router()
        if portal_router is not None:
            app.include_router(portal_router)
            logger.info("Content Portal routes registered")

    # Name every request body that still silently drops unknown fields.
    from callobuzz_rag.api.schema_strictness import audit_request_schema_strictness

    audit_request_schema_strictness(app)

    return app
