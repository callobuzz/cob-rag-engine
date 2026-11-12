"""Context assembly — builds structured LLM prompts from retrieved chunks.

Takes reranked search results, loads chunk text from the database,
applies token budgeting, and produces a list of LLMMessage ready for
the generation step.
"""

import logging
import sys
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from callobuzz_rag.chunking.token_counter import TokenCounter
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.retrieval.prompt_templates import (
    CONSTRAINTS_BLOCK,
    CONTEXT_HEADER,
    QUESTION_PREFIX,
    SYSTEM_PROMPT,
    format_citation,
)
from callobuzz_rag.retrieval.safety import ContentSanitizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CitedChunk:
    """A chunk selected for inclusion in the prompt, with its citation index.

    `text` is the chunk as stored — what API callers, citations and the widget show.
    `prompt_text` is the same text wrapped by the content sanitizer; use it whenever
    the chunk is handed to an LLM (it is None when no sanitizer is configured).
    """

    citation_index: int
    chunk_id: uuid.UUID
    text: str
    file_name: str | None
    page: int | None
    score: float
    token_count: int
    document_id: uuid.UUID | None = None
    external_id: str | None = None
    prompt_text: str | None = None
    section: str | None = None  # heading path, "A > B"
    audience: str | None = None  # the document's `audience` tag, when it has one (COB9)
    updated_at: str | None = None  # TV5 freshness: "YYYY-MM-DD" the version last changed

    @property
    def llm_text(self) -> str:
        return self.prompt_text if self.prompt_text is not None else self.text


@dataclass(frozen=True, slots=True)
class AssembledContext:
    """Output of context assembly — everything needed for generation."""

    messages: list[LLMMessage]
    cited_chunks: list[CitedChunk]
    total_context_tokens: int
    chunks_included: int
    chunks_dropped: int


@dataclass(frozen=True, slots=True)
class ContextSearchResult:
    """Minimal search result input for context assembly."""

    id: uuid.UUID
    score: float
    payload: dict[str, str | int | float | bool | None] = field(default_factory=dict)


class ContextAssembler:
    """Assembles retrieved chunks into a token-budgeted LLM prompt.

    Usage:
        assembler = ContextAssembler(settings)
        result = await assembler.assemble(session, search_results, query)
        # result.messages is ready for LLMProvider.complete()
    """

    def __init__(
        self,
        settings: Settings,
        *,
        token_counter: TokenCounter | None = None,
        sanitizer: ContentSanitizer | None = None,
    ) -> None:
        self._context_max_tokens = settings.context_max_tokens
        self._response_reserve = settings.response_reserve_tokens
        self._counter = token_counter or TokenCounter()
        self._sanitizer = sanitizer

    async def assemble(
        self,
        session: AsyncSession,
        results: list[ContextSearchResult],
        query: str,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        constraints: str = CONSTRAINTS_BLOCK,
        response_language: str | None = None,
        preserve_order: bool = False,
        token_budget: bool = True,
        updated_tz: str | None = None,  # TV5 freshness: time zone; set = dates in the prompt
    ) -> AssembledContext:
        """Build the full prompt from search results.

        Args:
            session: Async DB session for loading chunk text.
            results: Ranked search results (highest score first).
            query: The user's original query string.
            system_prompt: Override system prompt (default: standard RAG prompt).
            constraints: Override constraints block (default: standard constraints).
            response_language: If set, appended to constraints as a language directive.
            preserve_order: Trust the caller's ordering instead of re-sorting by
                `score`. The orchestrator hands over a list already ranked
                by rerank, MMR and temporal decay; re-sorting it by `score` — the
                RRF fusion score, which none of those stages writes back — undoes
                all three. It matters beyond ordering because the token budget
                below drops the TAIL, so the wrong sort drops the chunk the
                cross-encoder ranked first. Off by default: the historical
                behaviour is wrong, but changing it changes every caller's output.
            token_budget: Fit the chunks into `context_max_tokens`. That budget is
                the LLM prompt's; retrieval-only callers (POST /query/retrieve) pass
                False and get every ranked chunk up to `final_k`.

        Returns:
            AssembledContext with messages list and citation metadata.
        """
        # 1. Load chunks from DB
        chunk_map = await self._load_chunks(session, [r.id for r in results])

        # 2. Order the results.
        #
        # Sorting by `score` is only correct when `score` is what the pipeline
        # last decided by. MMR is the case that proves a sort can never be
        # sufficient: it reorders for DIVERSITY, so its output is deliberately
        # not score-monotone and no write-back can make a re-sort reproduce it.
        sorted_results = (
            list(results)
            if preserve_order
            else sorted(results, key=lambda r: r.score, reverse=True)
        )

        # 3. Calculate available token budget
        effective_constraints = constraints
        if response_language:
            effective_constraints += f"\n- You MUST respond in {response_language}."
        system_tokens = self._counter.count(system_prompt)
        query_section = f"{QUESTION_PREFIX}{query}\n\n{effective_constraints}"
        query_tokens = self._counter.count(query_section)
        overhead_tokens = self._counter.count(f"{CONTEXT_HEADER}\n\n")
        available = (
            self._context_max_tokens
            - system_tokens
            - query_tokens
            - overhead_tokens
            - self._response_reserve
        )

        if not token_budget:
            available = sys.maxsize
        elif available <= 0:
            logger.warning(
                "No token budget for context: max=%d, system=%d, query=%d, reserve=%d",
                self._context_max_tokens,
                system_tokens,
                query_tokens,
                self._response_reserve,
            )
            return self._build_empty(system_prompt, query)

        # 4. Fill context greedily by score
        cited_chunks: list[CitedChunk] = []
        formatted_citations: list[str] = []
        used_tokens = 0
        citation_index = 1
        dropped = 0

        for result in sorted_results:
            chunk = chunk_map.get(result.id)
            if chunk is None:
                logger.warning("Chunk %s not found in DB, skipping", result.id)
                dropped += 1
                continue

            # Get source metadata via relationships
            document = self._get_document(chunk)
            file_name = document.file_name if document is not None else None
            chunk_text = chunk.text

            # Apply sanitizer if available (Epic 2-11)
            if self._sanitizer is not None:
                chunk_text = self._sanitizer.sanitize(chunk_text, source_label=file_name)

            section = " > ".join(p.strip() for p in chunk.structure_path or [] if p.strip())
            updated = self._updated_on(chunk, updated_tz)  # TV5 freshness
            formatted = format_citation(
                citation_index, chunk_text, file_name, chunk.page, section or None, updated
            )
            chunk_tokens = self._counter.count(formatted)

            if used_tokens + chunk_tokens > available:
                dropped += 1
                continue

            cited_chunks.append(
                CitedChunk(
                    citation_index=citation_index,
                    chunk_id=chunk.id,
                    text=chunk.text,
                    file_name=file_name,
                    page=chunk.page,
                    score=result.score,
                    token_count=chunk_tokens,
                    document_id=document.id if document is not None else None,
                    external_id=document.external_id if document is not None else None,
                    prompt_text=chunk_text if self._sanitizer is not None else None,
                    section=section or None,
                    audience=self._audience_of(document),
                    updated_at=updated,  # TV5 freshness
                )
            )
            formatted_citations.append(formatted)
            used_tokens += chunk_tokens
            citation_index += 1

        # 5. Construct messages
        context_block = CONTEXT_HEADER + "\n"
        for fc in formatted_citations:
            context_block += fc
            context_block += "\n\n"

        user_content = f"{context_block}{QUESTION_PREFIX}{query}\n\n{effective_constraints}"
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_content),
        ]

        logger.info(
            "Context assembled: %d chunks (%d tokens), %d dropped",
            len(cited_chunks),
            used_tokens,
            dropped,
        )

        return AssembledContext(
            messages=messages,
            cited_chunks=cited_chunks,
            total_context_tokens=used_tokens,
            chunks_included=len(cited_chunks),
            chunks_dropped=dropped,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_chunks(
        self,
        session: AsyncSession,
        chunk_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, Chunk]:
        """Batch-load chunks with document relationships for source metadata."""
        if not chunk_ids:
            return {}

        from callobuzz_rag.db.models.document_version import DocumentVersion

        stmt = (
            select(Chunk)
            .options(joinedload(Chunk.document_version).joinedload(DocumentVersion.document))
            .where(Chunk.id.in_(chunk_ids))
        )
        result = await session.execute(stmt)
        chunks = result.scalars().unique().all()
        return {c.id: c for c in chunks}

    @staticmethod
    def _updated_on(chunk: Chunk, tz: str | None) -> str | None:  # TV5 freshness
        """The date the chunk's version was created, in `tz`; None when not asked for or when
        the version has no date. Never raises: a date is a nicety, not a reason to fail."""
        if tz is None:
            return None
        from callobuzz_rag.conversation.freshness import updated_on

        try:
            return updated_on(chunk.document_version.created_at, tz)
        except AttributeError:
            return None

    @staticmethod
    def _audience_of(document: Document | None) -> str | None:
        meta = getattr(document, "doc_metadata", None)
        value = meta.get("audience") if isinstance(meta, dict) else None
        return value if isinstance(value, str) else None

    @staticmethod
    def _get_document(chunk: Chunk) -> Document | None:
        """Resolve chunk -> document_version -> document."""
        try:
            dv = chunk.document_version
            if dv is not None and dv.document is not None:
                document: Document = dv.document
                return document
        except AttributeError:
            pass
        return None

    def _build_empty(self, system_prompt: str, query: str) -> AssembledContext:
        """Return an assembled context with no chunks (budget exhausted)."""
        user_content = (
            f"{CONTEXT_HEADER}\n(No relevant context found)\n\n"
            f"{QUESTION_PREFIX}{query}\n\n{CONSTRAINTS_BLOCK}"
        )
        return AssembledContext(
            messages=[
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_content),
            ],
            cited_chunks=[],
            total_context_tokens=0,
            chunks_included=0,
            chunks_dropped=0,
        )
