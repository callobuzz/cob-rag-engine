"""Search mode: matching help pages instead of a written answer.

Used when the workspace has used its monthly questions, and when no language-model key is
available. It runs retrieval only (no query rewriting, no re-ranking, no model call at all) over
the COB knowledge base with the caller's audience filter, and returns up to five distinct help
pages. Free by construction.
"""

from typing import Any, Final

from callobuzz_rag.api.schemas.cob import CobContext, CobSource
from callobuzz_rag.cob.events import cob_source, platform_source
from callobuzz_rag.cob.kb import parse_external_id
from callobuzz_rag.cob.platform import platform_hit
from callobuzz_rag.cob.types import CobRole, DocsIndex, allowed_audiences

MAX_RESULTS: Final = 5
POOL: Final = 12


async def search(
    orchestrator: Any,
    corpus_id: Any,
    text: str,
    *,
    index: DocsIndex,
    role: CobRole,
    context: CobContext | None,
) -> list[CobSource]:
    """The top distinct help pages for `text` that `role` may read."""
    result = await orchestrator.run_retrieval_pipeline(
        text,
        corpus_id,
        top_k=POOL,
        final_k=POOL,
        enable_hyde=False,
        enable_classification=False,
        enable_mmr=False,
        enable_reranking=False,
        metadata_filters={"audience": allowed_audiences(role)},
    )
    out: list[CobSource] = []
    seen: set[str] = set()
    for chunk in result.assembled.cited_chunks:
        parsed = parse_external_id(chunk.external_id or "")
        if parsed is None:
            hit = platform_hit(
                external_id=chunk.external_id,
                document_id=chunk.document_id,
                file_name=chunk.file_name,
                audience=getattr(chunk, "audience", None),
                allowed=allowed_audiences(role),
            )
            if hit is not None and hit.slug not in seen:
                seen.add(hit.slug)
                out.append(platform_source(hit, len(out) + 1, {"text": chunk.text}))
                if len(out) == MAX_RESULTS:
                    break
            continue
        if parsed[0] in seen:
            continue
        source = cob_source(
            index,
            position=len(out) + 1,
            slug=parsed[0],
            anchor=parsed[1],
            text=chunk.text,
            role=role,
            context=context,
        )
        if source is None:
            continue
        seen.add(parsed[0])
        out.append(source)
        if len(out) == MAX_RESULTS:
            break
    return out
