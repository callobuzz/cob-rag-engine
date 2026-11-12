"""Fuzz tests for document parsing error handling.

Ensures corrupted, truncated, and malformed files raise appropriate errors
instead of crashing the pipeline.
"""

import pytest

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.parsing.docling_parser import DoclingParserProvider
from callobuzz_rag.parsing.errors import CorruptedFileError, ParsingError
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement

CORRUPTED_INPUTS = [
    ("empty.pdf", b"", "Empty file"),
    ("truncated.pdf", b"%PDF-1.4 truncated content", "Truncated PDF header"),
    ("wrong_extension.pdf", b"This is just plain text, not a PDF", "Text with wrong extension"),
    ("binary_garbage.pdf", bytes(range(256)) * 10, "Random binary data"),
    ("null_bytes.pdf", b"\x00" * 1024, "Null byte file"),
    ("html_as_pdf.pdf", b"<html><body>Not a PDF</body></html>", "HTML masquerading as PDF"),
]


@pytest.mark.slow
@pytest.mark.parametrize("filename,content,description", CORRUPTED_INPUTS)
async def test_corrupted_file_raises_error(filename, content, description, tmp_path):
    """Corrupted files should raise ParsingError or subclass, never crash."""
    parser = DoclingParserProvider()
    file_path = tmp_path / filename
    file_path.write_bytes(content)

    with pytest.raises((ParsingError, CorruptedFileError)):
        await parser.parse(file_path, "application/pdf")


@pytest.mark.slow
async def test_nonexistent_file_raises_error(tmp_path):
    """Missing file should raise an error."""
    parser = DoclingParserProvider()
    with pytest.raises((ParsingError, FileNotFoundError, OSError)):
        await parser.parse(tmp_path / "does_not_exist.pdf", "application/pdf")


async def test_oversized_chunk_does_not_crash():
    """Very long text should be chunked, not cause OOM."""
    # Use sentences (period-terminated) so the chunker can split at boundaries
    long_sentence = "This is a test sentence with several words. "
    doc = ParsedDocument(
        elements=[
            ParsedElement(type=ElementType.PARAGRAPH, text=long_sentence * 5_000),
        ],
        source_filename="big.txt",
        parser_name="test",
    )
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(doc)
    assert len(chunks) > 1  # Must be split
    assert all(c.text for c in chunks)  # No empty chunks
