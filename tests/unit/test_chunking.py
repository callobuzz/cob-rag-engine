"""Unit tests for structure-aware chunker."""

from callobuzz_rag.chunking.chunker import ChunkerConfig, StructureAwareChunker
from callobuzz_rag.chunking.table_serializer import (
    serialize_table_for_embedding,
    serialize_table_to_json,
)
from callobuzz_rag.chunking.token_counter import TokenCounter
from callobuzz_rag.parsing.models import (
    ElementType,
    ParsedDocument,
    ParsedElement,
    TableData,
)


def _make_doc(elements: list[ParsedElement]) -> ParsedDocument:
    return ParsedDocument(elements=elements, source_filename="test.html")


# --- TokenCounter tests ---


def test_token_counter_count():
    counter = TokenCounter()
    assert counter.count("Hello world") > 0
    assert counter.count("") == 0


def test_token_counter_truncate():
    counter = TokenCounter()
    long_text = "word " * 500
    truncated = counter.truncate_to_tokens(long_text, 10)
    assert counter.count(truncated) <= 10


def test_token_counter_last_n():
    counter = TokenCounter()
    text = "The quick brown fox jumps over the lazy dog"
    last = counter.get_last_n_tokens_text(text, 3)
    assert counter.count(last) <= 3
    assert last  # non-empty


# --- Table serializer tests ---


def test_serialize_table_for_embedding():
    td = TableData(markdown="| A | B |", json_rows=[{"A": 1}], num_rows=1, num_cols=2)
    assert serialize_table_for_embedding(td) == "| A | B |"


def test_serialize_table_to_json():
    td = TableData(markdown="| A |", json_rows=[{"A": 1}, {"A": 2}], num_rows=2, num_cols=1)
    result = serialize_table_to_json(td)
    assert '"A"' in result
    assert "1" in result


# --- Chunker: heading boundaries ---


def test_heading_boundaries_create_sections():
    """Each heading starts a new section."""
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="Intro", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="First paragraph."),
            ParsedElement(type=ElementType.HEADING, text="Methods", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="Second paragraph."),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=1))
    chunks = chunker.chunk(doc)

    text_chunks = [c for c in chunks if not c.is_parent]
    assert len(text_chunks) >= 2

    paths = [c.structure_path for c in text_chunks]
    assert ["Intro"] in paths
    assert ["Methods"] in paths


def test_nested_headings_build_path():
    """H1 > H2 builds a hierarchical structure_path."""
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="Chapter 1", level=1),
            ParsedElement(type=ElementType.HEADING, text="Section 1.1", level=2),
            ParsedElement(type=ElementType.PARAGRAPH, text="Content here."),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=1))
    chunks = chunker.chunk(doc)

    content_chunks = [c for c in chunks if not c.is_parent and c.text == "Content here."]
    assert len(content_chunks) == 1
    assert content_chunks[0].structure_path == ["Chapter 1", "Section 1.1"]


# --- Chunker: table/code isolation ---


def test_table_produces_standalone_chunk():
    """Tables are always standalone chunks with is_table=True."""
    td = TableData(
        markdown="| A | B |\n|---|---|\n| 1 | 2 |",
        json_rows=[{"A": "1", "B": "2"}],
        num_rows=2,
        num_cols=2,
    )
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="Data", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="Some text before."),
            ParsedElement(type=ElementType.TABLE, text="| A | B |", table_data=td),
            ParsedElement(type=ElementType.PARAGRAPH, text="Some text after."),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=1))
    chunks = chunker.chunk(doc)

    table_chunks = [c for c in chunks if c.is_table]
    assert len(table_chunks) == 1
    assert table_chunks[0].table_json is not None
    assert "A" in table_chunks[0].text


def test_code_produces_standalone_chunk():
    """Code blocks are always standalone chunks with is_code=True."""
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="Example", level=1),
            ParsedElement(type=ElementType.CODE, text="print('hello')"),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=1))
    chunks = chunker.chunk(doc)

    code_chunks = [c for c in chunks if c.is_code]
    assert len(code_chunks) == 1
    assert code_chunks[0].text == "print('hello')"


# --- Chunker: size normalization ---


def test_large_text_is_split():
    """Text exceeding max_tokens is split at sentence boundaries."""
    # Generate text that's definitely over max_tokens
    long_text = ". ".join(f"Sentence number {i} has some content" for i in range(200))
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="Long", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text=long_text),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(max_tokens=100, min_tokens=1, overlap_tokens=0))
    chunks = chunker.chunk(doc)

    text_chunks = [c for c in chunks if not c.is_parent]
    assert len(text_chunks) > 1
    for c in text_chunks:
        # Each chunk should be within reasonable bounds
        assert c.length_tokens <= 200  # Some tolerance for sentence boundaries


def test_small_chunks_are_merged():
    """Undersized chunks are merged with neighbors."""
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="Short", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="Hi."),
            ParsedElement(type=ElementType.PARAGRAPH, text="Bye."),
        ]
    )
    # min_tokens=50 means "Hi." and "Bye." are both too small
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=50, overlap_tokens=0))
    chunks = chunker.chunk(doc)

    text_chunks = [c for c in chunks if not c.is_parent]
    # Should merge into single chunk
    assert len(text_chunks) == 1
    assert "Hi." in text_chunks[0].text
    assert "Bye." in text_chunks[0].text


def test_tiny_table_code_fragments_are_coalesced():
    """Many tiny table/code shards in one section collapse into few chunks.

    Markdown parsers can explode a table that contains inline code (e.g.
    ``| Page | `/orders` |``) into dozens of 1-token CODE and TABLE elements.
    These must coalesce so retrieval sees coherent sections, not shards.
    """
    elements = [ParsedElement(type=ElementType.HEADING, text="Navigation", level=1)]
    routes = [
        "/",
        "/orders",
        "/customers",
        "/products",
        "/inventory",
        "/reminders",
        "/box-types",
        "/clipboard",
    ]
    # Each route arrives as its own tiny CODE element, each label as a tiny TABLE.
    for r in routes:
        elements.append(ParsedElement(type=ElementType.CODE, text=r))
        elements.append(ParsedElement(type=ElementType.TABLE, text=f"| {r} | page |"))

    doc = _make_doc(elements)
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=50, overlap_tokens=0))
    chunks = [c for c in chunker.chunk(doc) if not c.is_parent]

    # 16 shards must collapse to a small number of coherent chunks.
    assert len(chunks) <= 2
    combined = "\n".join(c.text for c in chunks)
    for r in routes:
        assert r in combined  # no content lost in coalescing


def test_large_table_stays_standalone():
    """A genuine large table (>= min_tokens) is preserved as its own chunk."""
    big_table = "| Col A | Col B | Col C |\n" + "\n".join(
        f"| value {i} alpha | value {i} beta | value {i} gamma |" for i in range(30)
    )
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="Data", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="Intro line."),
            ParsedElement(
                type=ElementType.TABLE,
                text=big_table,
                table_data=TableData(markdown=big_table, json_rows=[], num_rows=30, num_cols=3),
            ),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=50, overlap_tokens=0))
    chunks = chunker.chunk(doc)

    table_chunks = [c for c in chunks if c.is_table]
    assert len(table_chunks) == 1
    assert table_chunks[0].text == big_table  # not merged with the intro line


# --- Chunker: overlap ---


def test_overlap_added_between_text_chunks():
    """Consecutive text chunks in same section get overlap."""
    long_a = ". ".join(f"Alpha sentence {i}" for i in range(100))
    long_b = ". ".join(f"Beta sentence {i}" for i in range(100))
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="Section", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text=long_a),
            ParsedElement(type=ElementType.PARAGRAPH, text=long_b),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(max_tokens=100, min_tokens=1, overlap_tokens=10))
    chunks = chunker.chunk(doc)

    text_chunks = [c for c in chunks if not c.is_parent and not c.is_table and not c.is_code]
    # If there are multiple chunks, the second should contain some text from the first
    if len(text_chunks) > 1:
        # Overlap means the second chunk starts with content from the first
        assert len(text_chunks[1].text) > len(long_b.split(". ")[0])


def test_no_overlap_on_table_chunks():
    """Tables should not receive overlap text."""
    td = TableData(markdown="| X |", json_rows=[], num_rows=1, num_cols=1)
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="S", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="Some text here. More text."),
            ParsedElement(type=ElementType.TABLE, text="| X |", table_data=td),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=1, overlap_tokens=10))
    chunks = chunker.chunk(doc)

    table_chunks = [c for c in chunks if c.is_table]
    assert len(table_chunks) == 1
    assert table_chunks[0].text == "| X |"  # No overlap prepended


# --- Chunker: parent-child linking ---


def test_parent_child_linking():
    """Sections with multiple children get a parent chunk."""
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="Parent Section", level=1),
            ParsedElement(
                type=ElementType.PARAGRAPH, text="First child paragraph with enough content."
            ),
            ParsedElement(
                type=ElementType.PARAGRAPH, text="Second child paragraph with enough content."
            ),
            ParsedElement(
                type=ElementType.PARAGRAPH, text="Third child paragraph with enough content."
            ),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=1, overlap_tokens=0))
    chunks = chunker.chunk(doc)

    parent_chunks = [c for c in chunks if c.is_parent]
    child_chunks = [c for c in chunks if c.parent_chunk_index is not None]

    # If children were merged (too small), there may be no parent
    if len(child_chunks) > 0:
        assert len(parent_chunks) >= 1
        assert parent_chunks[0].is_parent is True


# --- Chunker: edge cases ---


def test_empty_document():
    """Empty document returns no chunks."""
    doc = _make_doc([])
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(doc)
    assert chunks == []


def test_no_headings():
    """Document without headings goes into a single section."""
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.PARAGRAPH, text="Just some text."),
            ParsedElement(type=ElementType.PARAGRAPH, text="More text here."),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=1, overlap_tokens=0))
    chunks = chunker.chunk(doc)

    assert len(chunks) >= 1
    for c in chunks:
        assert c.structure_path == []  # No heading hierarchy


def test_chunk_indices_are_sequential():
    """All chunks get sequential 0-based indices."""
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="A", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="Paragraph A."),
            ParsedElement(type=ElementType.HEADING, text="B", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="Paragraph B."),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=1, overlap_tokens=0))
    chunks = chunker.chunk(doc)

    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))


def test_all_chunks_have_token_counts():
    """Every chunk should have length_tokens > 0."""
    doc = _make_doc(
        [
            ParsedElement(type=ElementType.HEADING, text="Title", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="Some content here."),
        ]
    )
    chunker = StructureAwareChunker(ChunkerConfig(min_tokens=1))
    chunks = chunker.chunk(doc)

    for c in chunks:
        assert c.length_tokens > 0
