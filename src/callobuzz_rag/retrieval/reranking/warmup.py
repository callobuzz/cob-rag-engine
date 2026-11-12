"""Loading the cross-encoder at start, when something will actually use it.

The model is 2+ GB and takes 6 to 18 s to load; a lazy load charges that to whichever
visitor asks the first question after every deploy. So the engine loads it in the
background at start, but only when a question can reach it: re-ranking is on for the
whole engine, or at least one knowledge base turned it on with the built-in re-ranker.
An engine whose knowledge bases all leave re-ranking off never pays the memory.
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)


def corpus_uses_local_reranker(
    retrieval_config: dict[str, Any] | None, settings: "Settings"
) -> bool:
    """Does this knowledge base's stored retrieval config run the built-in re-ranker?

    A field it does not store follows the engine default, exactly as `ConfigResolver`
    resolves it. An unreadable provider is treated as `local`, as the resolver does.
    """
    config = retrieval_config or {}
    enabled = config.get("reranking_enabled")
    if enabled is None:
        enabled = settings.reranking_enabled
    provider = config.get("rerank_provider")
    if provider is None:
        provider = settings.rerank_provider
    return bool(enabled) and str(provider).lower() != "llm"


async def any_corpus_uses_local_reranker(
    session_factory: Callable[[], Any], settings: "Settings"
) -> bool:
    """True when at least one knowledge base runs the built-in re-ranker."""
    from callobuzz_rag.db.models.corpus import Corpus

    async with session_factory() as session:
        result = await session.execute(select(Corpus.retrieval_config))
        return any(corpus_uses_local_reranker(config, settings) for config in result.scalars())


async def should_warm_reranker(settings: "Settings", session_factory: Callable[[], Any]) -> bool:
    """RAG_RERANK_WARMUP_ENABLED when set; otherwise: is the built-in re-ranker in use?

    Re-ranking switched on for the whole engine counts, as before. So does any single
    knowledge base that enabled it, which the old check (engine setting only) missed: a
    preset or the dashboard enables it per knowledge base, and the first visitor then
    paid for the load.
    """
    if settings.rerank_warmup_enabled is not None:
        return bool(settings.rerank_warmup_enabled)
    if settings.reranking_enabled and settings.rerank_provider != "llm":
        return True
    try:
        return await any_corpus_uses_local_reranker(session_factory, settings)
    except Exception as exc:  # the database may not be up yet; the lazy load still works
        logger.warning("Could not check whether any knowledge base re-ranks: %s", exc)
        return False


async def warm_reranker(
    reranker: Any, settings: "Settings", session_factory: Callable[[], Any]
) -> bool:
    """The background task: decide, load, log. Never raises. True when it warmed."""
    try:
        if not await should_warm_reranker(settings, session_factory):
            logger.info("Cross-encoder not warmed: no knowledge base uses the built-in re-ranker")
            return False
        logger.info("Warming the cross-encoder in the background")
        took = await reranker.warmup()
        logger.info("Cross-encoder warmed up in %.1fs", took)
        return True
    except Exception as exc:  # never fatal: the first re-ranked question loads it instead
        logger.warning("Cross-encoder warmup failed (first query will be slow): %s", exc)
        return False
