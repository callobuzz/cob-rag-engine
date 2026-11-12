"""Unit tests for ContextAssembler.assemble() method."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.retrieval.context_assembly import (
    ContextAssembler,
    ContextSearchResult,
)


def _mock_settings(**overrides: object) -> MagicMock:
    defaults = {
        "context_max_tokens": 200,
        "response_reserve_tokens": 20,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _mock_chunk(
    chunk_id: uuid.UUID,
    text: str = "Chunk text content",
    page: int | None = 1,
    file_name: str | None = "report.pdf",
) -> MagicMock:
    """Create a mock Chunk ORM object with document chain."""
    chunk = MagicMock()
    chunk.id = chunk_id
    chunk.text = text
    chunk.page = page
    doc = MagicMock()
    doc.file_name = file_name
    dv = MagicMock()
    dv.document = doc
    chunk.document_version = dv
    return chunk


def _mock_session(chunks: list[MagicMock]) -> AsyncMock:
    """Create a mock session that returns chunks from execute()."""
    session = AsyncMock()
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.unique.return_value.all.return_value = chunks
    result_mock.scalars.return_value = scalars_mock
    session.execute = AsyncMock(return_value=result_mock)
    return session


def _fixed_counter(count: int = 10) -> MagicMock:
    """Token counter that returns a fixed count for any text."""
    counter = MagicMock()
    counter.count = MagicMock(return_value=count)
    return counter


class TestContextAssembler:
    @pytest.mark.asyncio
    async def test_assemble_basic(self) -> None:
        """Happy path: 2 chunks fit in budget, both included."""
        id1, id2 = uuid.uuid4(), uuid.uuid4()
        chunks = [
            _mock_chunk(id1, "First chunk"),
            _mock_chunk(id2, "Second chunk"),
        ]
        session = _mock_session(chunks)
        assembler = ContextAssembler(
            _mock_settings(context_max_tokens=500),
            token_counter=_fixed_counter(10),
        )
        results = [
            ContextSearchResult(id=id1, score=0.9),
            ContextSearchResult(id=id2, score=0.8),
        ]
        assembled = await assembler.assemble(session, results, "test query")
        assert assembled.chunks_included == 2
        assert assembled.chunks_dropped == 0
        assert len(assembled.cited_chunks) == 2

    @pytest.mark.asyncio
    async def test_assemble_budget_exhausted(self) -> None:
        """Third chunk exceeds budget, gets dropped."""
        ids = [uuid.uuid4() for _ in range(3)]
        chunks = [_mock_chunk(cid, f"Chunk {i}") for i, cid in enumerate(ids)]
        session = _mock_session(chunks)
        # Very tight budget: system+query+overhead = ~30, budget left ~10
        # Each chunk = 10 tokens, so only 1 fits
        counter = MagicMock()
        counter.count = MagicMock(return_value=10)
        assembler = ContextAssembler(
            _mock_settings(context_max_tokens=60, response_reserve_tokens=10),
            token_counter=counter,
        )
        results = [ContextSearchResult(id=cid, score=1.0 - i * 0.1) for i, cid in enumerate(ids)]
        assembled = await assembler.assemble(session, results, "query")
        assert assembled.chunks_included < 3
        assert assembled.chunks_dropped > 0

    @pytest.mark.asyncio
    async def test_no_token_budget_keeps_every_chunk(self) -> None:
        """Retrieval-only callers get every ranked chunk, whatever the prompt budget."""
        ids = [uuid.uuid4() for _ in range(3)]
        chunks = [_mock_chunk(cid, f"Chunk {i}") for i, cid in enumerate(ids)]
        assembler = ContextAssembler(
            _mock_settings(context_max_tokens=5, response_reserve_tokens=0),
            token_counter=_fixed_counter(10),
        )
        results = [ContextSearchResult(id=cid, score=1.0 - i * 0.1) for i, cid in enumerate(ids)]
        assembled = await assembler.assemble(
            _mock_session(chunks), results, "query", token_budget=False
        )
        assert [c.chunk_id for c in assembled.cited_chunks] == ids
        assert assembled.chunks_dropped == 0

    @pytest.mark.asyncio
    async def test_assemble_empty_results(self) -> None:
        """No search results -> _build_empty output."""
        session = _mock_session([])
        assembler = ContextAssembler(_mock_settings(), token_counter=_fixed_counter(10))
        assembled = await assembler.assemble(session, [], "query")
        assert assembled.chunks_included == 0
        assert assembled.chunks_dropped == 0
        assert len(assembled.messages) == 2

    @pytest.mark.asyncio
    async def test_assemble_zero_budget(self) -> None:
        """context_max_tokens too small -> _build_empty."""
        id1 = uuid.uuid4()
        chunks = [_mock_chunk(id1)]
        session = _mock_session(chunks)
        # Budget so small that overhead alone exceeds it
        assembler = ContextAssembler(
            _mock_settings(context_max_tokens=5, response_reserve_tokens=0),
            token_counter=_fixed_counter(10),
        )
        results = [ContextSearchResult(id=id1, score=0.9)]
        assembled = await assembler.assemble(session, results, "query")
        assert assembled.chunks_included == 0

    @pytest.mark.asyncio
    async def test_assemble_chunk_not_in_db(self) -> None:
        """Search result ID not in DB -> skipped with warning."""
        session = _mock_session([])  # No chunks returned
        assembler = ContextAssembler(
            _mock_settings(context_max_tokens=500),
            token_counter=_fixed_counter(10),
        )
        missing_id = uuid.uuid4()
        results = [ContextSearchResult(id=missing_id, score=0.9)]
        assembled = await assembler.assemble(session, results, "query")
        assert assembled.chunks_included == 0
        assert assembled.chunks_dropped == 1

    @pytest.mark.asyncio
    async def test_assemble_with_sanitizer(self) -> None:
        """ContentSanitizer.sanitize() called on each chunk text."""
        id1 = uuid.uuid4()
        chunks = [_mock_chunk(id1, "raw text")]
        session = _mock_session(chunks)
        sanitizer = MagicMock()
        sanitizer.sanitize = MagicMock(return_value="sanitized text")
        assembler = ContextAssembler(
            _mock_settings(context_max_tokens=500),
            token_counter=_fixed_counter(10),
            sanitizer=sanitizer,
        )
        results = [ContextSearchResult(id=id1, score=0.9)]
        assembled = await assembler.assemble(session, results, "query")
        sanitizer.sanitize.assert_called_once()
        # Callers and citations get the stored text; only the LLM sees the wrapped one.
        assert assembled.cited_chunks[0].text == "raw text"
        assert assembled.cited_chunks[0].prompt_text == "sanitized text"
        assert assembled.cited_chunks[0].llm_text == "sanitized text"
        assert "sanitized text" in assembled.messages[1].content
        assert "raw text" not in assembled.messages[1].content

    @pytest.mark.asyncio
    async def test_assemble_without_sanitizer(self) -> None:
        """No sanitizer injected -> raw text used."""
        id1 = uuid.uuid4()
        chunks = [_mock_chunk(id1, "raw text")]
        session = _mock_session(chunks)
        assembler = ContextAssembler(
            _mock_settings(context_max_tokens=500),
            token_counter=_fixed_counter(10),
        )
        results = [ContextSearchResult(id=id1, score=0.9)]
        assembled = await assembler.assemble(session, results, "query")
        assert assembled.cited_chunks[0].text == "raw text"

    @pytest.mark.asyncio
    async def test_assemble_citation_order(self) -> None:
        """Citations numbered [1], [2] in score order."""
        id1, id2 = uuid.uuid4(), uuid.uuid4()
        chunks = [
            _mock_chunk(id1, "Low score"),
            _mock_chunk(id2, "High score"),
        ]
        session = _mock_session(chunks)
        assembler = ContextAssembler(
            _mock_settings(context_max_tokens=500),
            token_counter=_fixed_counter(10),
        )
        # id2 has higher score
        results = [
            ContextSearchResult(id=id1, score=0.5),
            ContextSearchResult(id=id2, score=0.9),
        ]
        assembled = await assembler.assemble(session, results, "query")
        assert assembled.cited_chunks[0].citation_index == 1
        assert assembled.cited_chunks[0].score == 0.9  # highest first
        assert assembled.cited_chunks[1].citation_index == 2

    @pytest.mark.asyncio
    async def test_assemble_messages_structure(self) -> None:
        """Output has system + user messages, user contains context + query."""
        id1 = uuid.uuid4()
        chunks = [_mock_chunk(id1)]
        session = _mock_session(chunks)
        assembler = ContextAssembler(
            _mock_settings(context_max_tokens=500),
            token_counter=_fixed_counter(10),
        )
        results = [ContextSearchResult(id=id1, score=0.9)]
        assembled = await assembler.assemble(session, results, "my question")
        assert len(assembled.messages) == 2
        assert assembled.messages[0].role == "system"
        assert assembled.messages[1].role == "user"
        assert "my question" in assembled.messages[1].content
        assert "CONTEXT:" in assembled.messages[1].content

    def test_get_document_traversal(self) -> None:
        """Chunk -> DocumentVersion -> Document chain works."""
        chunk = _mock_chunk(uuid.uuid4(), file_name="test.pdf")
        document = ContextAssembler._get_document(chunk)
        assert document is not None
        assert document.file_name == "test.pdf"

    def test_get_document_missing(self) -> None:
        """DocumentVersion.document is None -> returns None."""
        chunk = MagicMock()
        chunk.document_version = MagicMock()
        chunk.document_version.document = None
        assert ContextAssembler._get_document(chunk) is None


@pytest.mark.asyncio
async def test_section_reaches_the_prompt_and_the_caller() -> None:
    chunk_id = uuid.uuid4()
    chunk = _mock_chunk(chunk_id, "Adds offline checkout.", page=None, file_name="release-notes.md")
    chunk.structure_path = ["Store app release notes", "Version 4.2.0"]
    assembler = ContextAssembler(
        _mock_settings(context_max_tokens=500), token_counter=_fixed_counter(10)
    )
    assembled = await assembler.assemble(
        _mock_session([chunk]), [ContextSearchResult(id=chunk_id, score=0.9)], "which release?"
    )
    assert assembled.cited_chunks[0].section == "Store app release notes > Version 4.2.0"
    assert "section: Store app release notes > Version 4.2.0" in assembled.messages[1].content
