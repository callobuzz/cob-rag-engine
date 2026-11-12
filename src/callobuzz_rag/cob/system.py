"""COB's built-in workspace and knowledge base.

COB answers from a read-only knowledge base of the engine's own documentation. It lives in a
workspace (`tenants.is_system`) and a knowledge base (`corpora.is_system`) of its own, each
allowed once by a partial unique index. They are created at app start, idempotently and under
an advisory lock (several uvicorn workers start together), on the built-in Internal plan.

The system rows are never listed to customers, never counted against a plan, and never changed
through the API: every write that reaches them answers 409 `system_resource_protected`.
Reading stays open to the platform operator.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select, text

from callobuzz_rag.api.errors import APIError

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant
    from callobuzz_rag.vectordb.base import VectorStorageProvider

logger = logging.getLogger(__name__)

SYSTEM_TENANT_NAME: Final = "COB"
SYSTEM_TENANT_DESCRIPTION: Final = "The engine's built-in assistant. Managed by the engine."
SYSTEM_CORPUS_NAME: Final = "COB docs"
SYSTEM_CORPUS_DESCRIPTION: Final = "The engine's own documentation, read by COB."
SYSTEM_COLLECTION: Final = "cob_docs"
LOCK_KEY: Final = "cob_system"
PROTECTED_DETAIL: Final = (
    "This is the engine's built-in COB workspace. It is managed by the engine and cannot be "
    "changed."
)
_READ_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class CobSystem:
    tenant_id: uuid.UUID
    corpus_id: uuid.UUID
    created: bool


def is_system_corpus(corpus: Any) -> bool:
    return getattr(corpus, "is_system", False) is True


def is_system_tenant(tenant: Any) -> bool:
    return getattr(tenant, "is_system", False) is True


def _protected() -> APIError:
    return APIError(409, PROTECTED_DETAIL, "system_resource_protected")


def refuse_system_tenant(tenant: Any) -> None:
    """409 `system_resource_protected` when `tenant` is COB's workspace."""
    if is_system_tenant(tenant):
        raise _protected()


def refuse_system_corpus(corpus: Any) -> None:
    """409 `system_resource_protected` when `corpus` is COB's knowledge base."""
    if is_system_corpus(corpus):
        raise _protected()


def refuse_system_write(request: "Request", corpus: Any) -> None:
    """For a handler that loads its own knowledge base: refuse every method that is not a
    plain read when it is COB's."""
    if is_system_corpus(corpus) and request.method not in _READ_METHODS:
        raise _protected()


def refuse_system_tenant_write(request: "Request", tenant: Any) -> None:
    if is_system_tenant(tenant) and request.method not in _READ_METHODS:
        raise _protected()


async def refuse_system_corpus_id(session: "AsyncSession", corpus_id: uuid.UUID | None) -> None:
    """`refuse_system_corpus` for a route that only has the knowledge base's id."""
    if corpus_id is None:
        return
    from callobuzz_rag.db.models.corpus import Corpus

    refuse_system_corpus(await session.get(Corpus, corpus_id))


async def get_system_corpus(session: "AsyncSession") -> "Corpus | None":
    from callobuzz_rag.db.models.corpus import Corpus

    return (
        await session.execute(select(Corpus).where(Corpus.is_system.is_(True)))
    ).scalar_one_or_none()


async def get_system_tenant(session: "AsyncSession") -> "Tenant | None":
    from callobuzz_rag.db.models.tenant import Tenant

    return (
        await session.execute(select(Tenant).where(Tenant.is_system.is_(True)))
    ).scalar_one_or_none()


async def ensure_cob_system(
    session: "AsyncSession",
    *,
    settings: "Settings",
    vector_store: "VectorStorageProvider | None" = None,
    lock: bool = True,
) -> CobSystem:
    """Create COB's workspace and knowledge base if they do not exist; change nothing if they
    do. The caller commits. `lock=False` is for tests whose session cannot run raw SQL."""
    from callobuzz_rag.accounts.operator import internal_plan
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant

    if lock:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY}
        )
    created = False
    tenant = await get_system_tenant(session)
    if tenant is None:
        plan = await internal_plan(session)
        tenant = Tenant(
            name=SYSTEM_TENANT_NAME,
            description=SYSTEM_TENANT_DESCRIPTION,
            api_key_hash="",
            status="active",
            activated_at=datetime.now(UTC),
            plan_id=plan.id,
            is_system=True,
        )
        session.add(tenant)
        await session.flush()
        created = True
    corpus = await get_system_corpus(session)
    if corpus is None:
        corpus = Corpus(
            name=SYSTEM_CORPUS_NAME,
            description=SYSTEM_CORPUS_DESCRIPTION,
            qdrant_collection=SYSTEM_COLLECTION,
            llm_config=None,
            applied_embedding=engine_embedding(settings),
            tenant_id=tenant.id,
            is_system=True,
        )
        session.add(corpus)
        await session.flush()
        created = True
        if vector_store is not None:
            try:
                await vector_store.create_collection(
                    SYSTEM_COLLECTION, int(settings.embedding_dimensions)
                )
            except Exception:  # the start-up self-healing loop retries on the next start
                logger.warning("Could not create the COB collection yet", exc_info=True)
    if created:
        logger.info("COB system workspace ready: tenant=%s corpus=%s", tenant.id, corpus.id)
    return CobSystem(tenant_id=tenant.id, corpus_id=corpus.id, created=created)


def engine_embedding(settings: "Settings") -> dict[str, Any]:
    """The embedding the engine builds new knowledge bases with."""
    return {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
    }
