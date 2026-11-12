"""Structure-aware chunker implementation.

Applies chunking rules in order:
1. Structural split at heading boundaries
2. Table/code block isolation
3. Size normalization (split oversized, merge tiny)
4. Overlap generation
5. Parent-child linking
"""

import logging
import re
from itertools import groupby
from typing import Any

from callobuzz_rag.chunking.models import ChunkResult
from callobuzz_rag.chunking.table_serializer import serialize_table_to_json
from callobuzz_rag.chunking.token_counter import TokenCounter
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement

logger = logging.getLogger(__name__)

# Sentence boundary regex: . ! ? and the Devanagari danda / double danda (Hindi, Marathi...)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।॥])\s+")


class ChunkerConfig:
    """Chunking configuration with sensible defaults."""

    def __init__(
        self,
        target_tokens: int = 300,
        min_tokens: int = 50,
        max_tokens: int = 1000,
        overlap_tokens: int = 64,
        encoding_name: str = "cl100k_base",
    ) -> None:
        self.target_tokens = target_tokens
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.encoding_name = encoding_name


def chunker_from_settings(settings: Any) -> "StructureAwareChunker":
    """Create a chunker configured from application settings."""
    return StructureAwareChunker(
        ChunkerConfig(
            target_tokens=settings.chunk_target_tokens,
            min_tokens=settings.chunk_min_tokens,
            max_tokens=settings.chunk_max_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        )
    )


class StructureAwareChunker:
    """Deterministic, rule-based document chunker."""

    def __init__(self, config: ChunkerConfig | None = None) -> None:
        self.config = config or ChunkerConfig()
        self.counter = TokenCounter(self.config.encoding_name)

    def chunk(self, document: ParsedDocument) -> list[ChunkResult]:
        """Process a parsed document into retrieval-optimized chunks."""
        # Step 1: Group elements into sections by headings
        sections = self._split_by_headings(document.elements)

        # Step 2 & 3: Process each section into chunks
        raw_chunks: list[ChunkResult] = []
        for section_path, elements in sections:
            section_chunks = self._process_section(section_path, elements)
            raw_chunks.extend(section_chunks)

        # What each chunk holds before overlap is prepended (duplicate detection hashes it)
        for raw in raw_chunks:
            raw.core_text = raw.text

        # Step 4: Add overlap between consecutive chunks
        chunks_with_overlap = self._add_overlap(raw_chunks)

        # Step 5: Add parent-child linking
        final_chunks = self._add_parent_child(chunks_with_overlap)

        # Assign final indices and token counts
        for i, chunk in enumerate(final_chunks):
            chunk.chunk_index = i
            chunk.length_tokens = self.counter.count(chunk.text)

        return final_chunks

    def _split_by_headings(
        self, elements: list[ParsedElement]
    ) -> list[tuple[list[str], list[ParsedElement]]]:
        """Split elements into sections at heading boundaries."""
        sections: list[tuple[list[str], list[ParsedElement]]] = []
        current_path: list[str] = []
        current_elements: list[ParsedElement] = []

        for elem in elements:
            if elem.type == ElementType.HEADING:
                # Save current section if it has content
                if current_elements:
                    sections.append((list(current_path), current_elements))
                    current_elements = []

                # Update heading path
                level = elem.level or 1
                # Trim path to current level, then append
                current_path = current_path[: level - 1]
                current_path.append(elem.text.strip())
            else:
                current_elements.append(elem)

        # Don't forget the last section
        if current_elements:
            sections.append((list(current_path), current_elements))

        return sections

    def _process_section(
        self, structure_path: list[str], elements: list[ParsedElement]
    ) -> list[ChunkResult]:
        """Process elements within a section into properly-sized chunks."""
        chunks: list[ChunkResult] = []
        text_buffer = ""

        for elem in elements:
            # Tables and code blocks are always standalone
            if elem.type == ElementType.TABLE:
                # Flush text buffer first
                if text_buffer.strip():
                    chunks.extend(self._normalize_size(text_buffer.strip(), structure_path))
                    text_buffer = ""

                table_text = elem.table_data.markdown if elem.table_data else elem.text
                table_json = serialize_table_to_json(elem.table_data) if elem.table_data else None
                chunks.append(
                    ChunkResult(
                        text=table_text,
                        chunk_index=0,
                        structure_path=list(structure_path),
                        page=elem.page,
                        is_table=True,
                        table_json=table_json,
                    )
                )
            elif elem.type == ElementType.CODE:
                if text_buffer.strip():
                    chunks.extend(self._normalize_size(text_buffer.strip(), structure_path))
                    text_buffer = ""

                chunks.append(
                    ChunkResult(
                        text=elem.text,
                        chunk_index=0,
                        structure_path=list(structure_path),
                        page=elem.page,
                        is_code=True,
                    )
                )
            else:
                text_buffer += elem.text + "\n\n"

        # Flush remaining text
        if text_buffer.strip():
            chunks.extend(self._normalize_size(text_buffer.strip(), structure_path))

        # Merge undersized chunks within the section
        chunks = self._merge_small_chunks(chunks)

        return chunks

    def _normalize_size(self, text: str, structure_path: list[str]) -> list[ChunkResult]:
        """Split text into chunks respecting token limits."""
        token_count = self.counter.count(text)

        # If within limits, return as single chunk
        if token_count <= self.config.max_tokens:
            return [
                ChunkResult(
                    text=text,
                    chunk_index=0,
                    structure_path=list(structure_path),
                )
            ]

        # Split at sentence boundaries
        sentences = SENTENCE_SPLIT_RE.split(text)
        chunks: list[ChunkResult] = []
        current_text = ""

        for sentence in sentences:
            candidate = (current_text + " " + sentence).strip() if current_text else sentence
            if self.counter.count(candidate) > self.config.max_tokens and current_text:
                chunks.append(
                    ChunkResult(
                        text=current_text.strip(),
                        chunk_index=0,
                        structure_path=list(structure_path),
                    )
                )
                current_text = sentence
            else:
                current_text = candidate

        if current_text.strip():
            chunks.append(
                ChunkResult(
                    text=current_text.strip(),
                    chunk_index=0,
                    structure_path=list(structure_path),
                )
            )

        return chunks

    def _merge_small_chunks(self, chunks: list[ChunkResult]) -> list[ChunkResult]:
        """Coalesce undersized fragments within a section, up to target size.

        A *real* table or code block (>= min_tokens) is kept standalone to
        preserve its structure. Everything else — small text chunks plus the
        many tiny table/code shards some markdown parsers emit for tables that
        contain inline code (e.g. ``| Page | `/orders` |`` exploded into
        dozens of 1-token elements) — is packed together in document order
        until it reaches target_tokens or a section boundary. This turns a
        section that arrived as 70 one-token shards into a few coherent chunks
        without disturbing normal prose chunking.
        """
        if len(chunks) <= 1:
            return chunks

        def is_real_block(chunk: ChunkResult) -> bool:
            # A standalone table/code unit worth preserving on its own.
            return (chunk.is_table or chunk.is_code) and self.counter.count(
                chunk.text
            ) >= self.config.min_tokens

        # Coalesce up to target size, but never past the hard max (handles
        # configs where max_tokens < target_tokens).
        cap = min(self.config.target_tokens, self.config.max_tokens)

        merged: list[ChunkResult] = []
        buffer: ChunkResult | None = None
        buffer_tokens = 0

        for chunk in chunks:
            if is_real_block(chunk):
                if buffer is not None:
                    merged.append(buffer)
                    buffer = None
                    buffer_tokens = 0
                merged.append(chunk)
                continue

            chunk_tokens = self.counter.count(chunk.text)
            if buffer is None:
                buffer = chunk
                buffer_tokens = chunk_tokens
                continue

            same_section = buffer.structure_path == chunk.structure_path
            # Only coalesce when at least one side is an undersized fragment —
            # never re-merge two already adequately-sized chunks (that would
            # undo legitimate size-normalization splits).
            has_small_fragment = (
                chunk_tokens < self.config.min_tokens or buffer_tokens < self.config.min_tokens
            )
            if same_section and has_small_fragment and buffer_tokens + chunk_tokens <= cap:
                buffer.text = (buffer.text + "\n" + chunk.text).strip()
                buffer_tokens += chunk_tokens
                # Result is only a pure table/code chunk if every fragment was.
                buffer.is_table = buffer.is_table and chunk.is_table
                buffer.is_code = buffer.is_code and chunk.is_code
            else:
                merged.append(buffer)
                buffer = chunk
                buffer_tokens = chunk_tokens

        if buffer is not None:
            merged.append(buffer)

        return merged

    def _add_overlap(self, chunks: list[ChunkResult]) -> list[ChunkResult]:
        """Add overlap from previous chunk to each chunk."""
        if self.config.overlap_tokens <= 0:
            return chunks

        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            curr = chunks[i]

            # Don't add overlap to table/code chunks or across section boundaries
            if curr.is_table or curr.is_code or prev.is_table or prev.is_code:
                continue
            if curr.structure_path != prev.structure_path:
                continue

            overlap_text = self.counter.get_last_n_tokens_text(
                prev.text, self.config.overlap_tokens
            )
            curr.text = overlap_text + "\n\n" + curr.text

        return chunks

    def _add_parent_child(self, chunks: list[ChunkResult]) -> list[ChunkResult]:
        """Create parent chunks for sections with multiple children."""
        result: list[ChunkResult] = []

        for _path, group in groupby(chunks, key=lambda c: tuple(c.structure_path)):
            children = list(group)

            # Only create parent if section has multiple non-parent children
            text_children = [c for c in children if not c.is_parent]
            if len(text_children) > 1:
                parent_text = "\n\n".join(
                    c.text for c in text_children if not c.is_table and not c.is_code
                )
                if parent_text.strip():
                    # Cap parent size — skip if over 2x max_tokens
                    if self.counter.count(parent_text) <= self.config.max_tokens * 2:
                        parent = ChunkResult(
                            text=parent_text,
                            chunk_index=0,
                            structure_path=list(_path),
                            is_parent=True,
                            core_text=parent_text,
                        )
                        result.append(parent)
                        parent_idx = len(result) - 1
                        for child in children:
                            child.parent_chunk_index = parent_idx
                            result.append(child)
                    else:
                        logger.debug("Skipping parent chunk for section %s — too large", _path)
                        result.extend(children)
                else:
                    result.extend(children)
            else:
                result.extend(children)

        return result
