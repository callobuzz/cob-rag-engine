"""The COB eval on the real knowledge base: runs in the worker after every docs sync.

Same grading as the offline gate (role filter, negative items, hit at five) but the retrieval is
the engine's own (dense plus keyword search over what the sync just stored, with the audience
filter). It never blocks the sync: a failing or crashing eval is recorded in `cob_status.eval`
and the knowledge base stays on the new docs. The operator sees the result on the Engine screen.
No language model is called; the query embeddings are the only spend.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from callobuzz_rag.cob.eval.items import EvalItem
from callobuzz_rag.cob.eval.offline import HIT_BAR, HIT_BAR_INDIC, PROBE_K, TOP_K
from callobuzz_rag.cob.kb import parse_external_id
from callobuzz_rag.cob.platform import DEFAULT_AUDIENCE, PLATFORM_SLUG_PREFIX
from callobuzz_rag.cob.types import Audience, DocsIndex, allowed_audiences
from callobuzz_rag.pipeline.records import SYSTEM_RECORD_PREFIX

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

STATUS_ID: Final = 1
_ALL_AUDIENCES: Final = tuple(a.value for a in Audience)
ERROR_LIMIT: Final = 500


@dataclass(frozen=True)
class SyncedHit:
    slug: str
    anchor: str
    text: str
    # Platform docs (COB9) only: the audience tag the real retrieval returned them with.
    audience: str | None = None


# (query, audiences, k) -> the top k sections the real retrieval returns for those audiences.
Search = Callable[[str, list[str], int], Awaitable[list[SyncedHit]]]


def _matches(item: EvalItem, hit: SyncedHit) -> bool:
    expect = item.expect
    return hit.slug == expect.slug and (expect.anchor is None or hit.anchor == expect.anchor)


def _audiences_of(index: DocsIndex, slug: str, anchor: str) -> set[str]:
    page = index.pages.get(slug)
    if page is None:
        return set()
    return {s.audience.value for s in page.sections if s.anchor == anchor}


async def run_synced_eval(
    search: Search, items: list[EvalItem], *, index: DocsIndex, now: datetime | None = None
) -> dict[str, Any]:
    """Grade `items` with the real retrieval; returns the summary stored in `cob_status.eval`."""
    answerable = answerable_indic = hits = hits_indic = 0
    leaks: list[str] = []
    negatives_failed: list[str] = []
    for item in items:
        allowed = allowed_audiences(item.cob_role)
        top = await search(item.search_text, allowed, TOP_K)
        for hit in top:
            if hit.slug.startswith(PLATFORM_SLUG_PREFIX):  # COB9: a page the operator added
                outside = (hit.audience or DEFAULT_AUDIENCE) not in allowed
            else:
                outside = not (_audiences_of(index, hit.slug, hit.anchor) <= set(allowed))
            if outside:
                leaks.append(item.id)
                break
        if item.is_negative:
            higher = [a for a in _ALL_AUDIENCES if a not in allowed]
            probe = await search(item.search_text, higher, PROBE_K)
            found = any(_matches(item, h) for h in probe)
            reachable = any(_matches(item, h) for h in top)
            leaked_text = any(n in h.text for h in top for n in item.expect.must_not_include)
            if reachable or leaked_text or not found:
                negatives_failed.append(item.id)
        elif item.expect.behaviour == "answer" and item.expect.slug:
            answerable += 1
            answerable_indic += item.is_indic
            if any(_matches(item, h) for h in top):
                hits += 1
                hits_indic += item.is_indic
    hit_rate = hits / answerable if answerable else 1.0
    hit_rate_indic = hits_indic / answerable_indic if answerable_indic else 1.0
    passed = (
        not leaks
        and not negatives_failed
        and hit_rate >= HIT_BAR
        and hit_rate_indic >= HIT_BAR_INDIC
    )
    return {
        "at": (now or datetime.now(UTC)).isoformat(),
        "version": index.version,
        "items": len(items),
        "hit_at_5": round(hit_rate, 4),
        "hit_at_5_indic": round(hit_rate_indic, 4),
        "role_leaks": len(leaks),
        "negatives_failed": len(negatives_failed),
        "passed": passed,
    }


def error_summary(
    index: DocsIndex, error: Exception, *, now: datetime | None = None
) -> dict[str, Any]:
    """What the status card shows when the evaluation itself could not run."""
    return {
        "at": (now or datetime.now(UTC)).isoformat(),
        "version": index.version,
        "passed": False,
        "error": str(error)[:ERROR_LIMIT] or type(error).__name__,
    }


async def store_eval(session: "AsyncSession", summary: dict[str, Any]) -> bool:
    """Write the summary on the status row. False when there is no status row."""
    from callobuzz_rag.db.models.cob_status import CobStatus

    row = await session.get(CobStatus, STATUS_ID)
    if row is None:
        return False
    row.eval = summary
    await session.commit()
    return True


class _NoModel:
    """The evaluation makes no language-model call; anything that tries is a bug."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"the COB evaluation makes no model calls (asked for {name})")


class SystemSearch:
    """A `Search` that owns the embedding clients it opened; `aclose()` releases them."""

    def __init__(self, search: Search, embedders: Sequence[Any]) -> None:
        self._search = search
        self._embedders = embedders

    async def __call__(self, query: str, audiences: list[str], k: int) -> list[SyncedHit]:
        return await self._search(query, audiences, k)

    async def aclose(self) -> None:
        for embedder in self._embedders:
            try:
                await embedder.close()
            except Exception:
                logger.warning("A COB eval embedding client did not close", exc_info=True)


def system_search(
    session: "AsyncSession", settings: "Settings", corpus: Any, vector_store: Any
) -> SystemSearch:
    """The engine's own retrieval over COB's knowledge base, as a `Search`: dense and keyword
    legs, the audience filter, no query rewriting, no re-ranking, no model. Call `aclose()` on
    the result when done: it closes the embedding clients."""
    from callobuzz_rag.embeddings.factory import create_embedding_provider, embedder_for_corpus
    from callobuzz_rag.retrieval.context_assembly import ContextAssembler
    from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
    from callobuzz_rag.retrieval.safety import ContentSanitizer

    default_embedder = create_embedding_provider(settings, cache=False)
    embedder = embedder_for_corpus(corpus, settings, default_embedder, cache=False)
    orchestrator = QueryOrchestrator(
        session=session,
        settings=settings,
        embedder=embedder,
        vector_store=vector_store,
        llm_provider=_NoModel(),  # type: ignore[arg-type]
        context_assembler=ContextAssembler(settings, sanitizer=ContentSanitizer(settings)),
        reranker=None,
        collection_name=corpus.qdrant_collection,
    )

    async def search(query: str, audiences: list[str], k: int) -> list[SyncedHit]:
        result = await orchestrator.run_retrieval_pipeline(
            query,
            corpus.id,
            top_k=max(k, 12),
            final_k=k,
            enable_hyde=False,
            enable_classification=False,
            enable_mmr=False,
            enable_reranking=False,
            metadata_filters={"audience": audiences},
            context_budget=False,
        )
        hits: list[SyncedHit] = []
        for chunk in result.assembled.cited_chunks:
            parsed = parse_external_id(chunk.external_id or "")
            if parsed is not None:
                hits.append(SyncedHit(parsed[0], parsed[1], chunk.text))
            elif chunk.document_id is not None and not (chunk.external_id or "").startswith(
                SYSTEM_RECORD_PREFIX
            ):
                slug = f"{PLATFORM_SLUG_PREFIX}{chunk.document_id}"
                hits.append(SyncedHit(slug, "", chunk.text, getattr(chunk, "audience", None)))
        return hits

    opened = [default_embedder] if embedder is default_embedder else [default_embedder, embedder]
    return SystemSearch(search, opened)


MakeSearch = Callable[["AsyncSession", Any], Search]


async def evaluate_after_sync(
    session_factory: Callable[[], Any],
    index: DocsIndex,
    make_search: MakeSearch,
    *,
    items: list[EvalItem] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run the synced eval and store its summary. Never raises: if the evaluation itself breaks
    the summary says so (`passed: false`, `error`), and the sync that came before stays done."""
    from callobuzz_rag.cob.eval.items import load_eval_items
    from callobuzz_rag.cob.system import get_system_corpus

    try:
        async with session_factory() as session:
            corpus = await get_system_corpus(session)
            if corpus is None:
                raise RuntimeError("COB's knowledge base does not exist yet")
            search = make_search(session, corpus)
            try:
                summary = await run_synced_eval(
                    search,
                    items if items is not None else load_eval_items(),
                    index=index,
                    now=now,
                )
            finally:
                closer = getattr(search, "aclose", None)
                if closer is not None:
                    try:
                        await closer()
                    except Exception:
                        logger.warning("The COB eval search did not close", exc_info=True)
    except Exception as exc:
        logger.error("The COB evaluation could not run", exc_info=True)
        summary = error_summary(index, exc, now=now)
    try:
        async with session_factory() as session:
            await store_eval(session, summary)
    except Exception:
        logger.error("The COB evaluation result could not be stored", exc_info=True)
    return summary
