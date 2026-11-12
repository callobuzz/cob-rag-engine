"""Golden tests for chunker output.

Ensures chunking is deterministic: same input always produces same output.
"""

import json
from pathlib import Path

import pytest

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement

GOLDEN_DIR = Path(__file__).parent / "expected"


def _make_sample_document() -> ParsedDocument:
    """Create a deterministic sample document for golden testing."""
    return ParsedDocument(
        elements=[
            ParsedElement(type=ElementType.HEADING, text="Introduction", level=1),
            ParsedElement(
                type=ElementType.PARAGRAPH,
                text=(
                    "This is the first paragraph. It contains some text about machine learning. "
                    "The field has grown rapidly in recent years."
                ),
            ),
            ParsedElement(type=ElementType.HEADING, text="Methods", level=2),
            ParsedElement(
                type=ElementType.PARAGRAPH,
                text=(
                    "We used a transformer architecture. The model was trained on a large dataset. "
                    "Results were evaluated using standard metrics."
                ),
            ),
        ],
        source_filename="sample.pdf",
        parser_name="test",
    )


def test_chunking_deterministic():
    """Same input always produces same output."""
    chunker = StructureAwareChunker()
    doc = _make_sample_document()

    chunks_1 = chunker.chunk(doc)
    chunks_2 = chunker.chunk(doc)

    assert len(chunks_1) == len(chunks_2)
    for c1, c2 in zip(chunks_1, chunks_2, strict=True):
        assert c1.text == c2.text
        assert c1.structure_path == c2.structure_path


def test_chunking_golden_output():
    """Compare against golden baseline.

    To update golden file, delete tests/golden/expected/sample_chunks.json and re-run.
    """
    chunker = StructureAwareChunker()
    doc = _make_sample_document()
    chunks = chunker.chunk(doc)

    output = [
        {
            "text": c.text,
            "structure_path": c.structure_path,
            "is_table": c.is_table,
            "is_code": c.is_code,
            "is_parent": c.is_parent,
        }
        for c in chunks
    ]

    golden_path = GOLDEN_DIR / "sample_chunks.json"
    if not golden_path.exists():
        # First run: create golden file
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(output, indent=2))
        pytest.skip("Golden file created — re-run to validate")

    expected = json.loads(golden_path.read_text())
    assert output == expected, "Chunking output differs from golden baseline"
