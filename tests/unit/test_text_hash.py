"""One normalisation for every text hash: block hashes and chunk hashes."""

from callobuzz_rag.chunking.chunker import ChunkerConfig, StructureAwareChunker
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.text_hash import normalize_text, text_hash


def test_normalisation_ignores_case_width_and_whitespace() -> None:
    assert text_hash("Free  Shipping\non orders ABOVE ₹999") == text_hash(
        "free shipping on orders above ₹999"
    )
    assert text_hash("ﬁle a claim") == text_hash("file a claim")  # NFKC ligature
    assert text_hash("Ｗarranty") == text_hash("warranty")  # noqa: RUF001  full-width letter


def test_normalisation_keeps_digits() -> None:
    assert text_hash("5 years") != text_hash("10 years")
    assert normalize_text("  A\t\tB \n C ") == "a b c"


def test_hash_is_16_hex_characters() -> None:
    h = text_hash("anything")
    assert len(h) == 16 and int(h, 16) >= 0


def _chunks(overlap: int, intro: str):
    sentences = [f"Sentence number {i} says something distinct about topic {i}." for i in range(60)]
    doc = ParsedDocument(
        elements=[
            ParsedElement(type=ElementType.HEADING, text="Policy", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text=intro),
            ParsedElement(type=ElementType.PARAGRAPH, text=" ".join(sentences)),
        ],
        parser_name="t",
        parser_version="1",
    )
    chunker = StructureAwareChunker(
        ChunkerConfig(target_tokens=60, min_tokens=5, max_tokens=120, overlap_tokens=overlap)
    )
    return chunker.chunk(doc)


def test_text_hash_normalization_ignores_overlap() -> None:
    # Overlap is added after the cut, so the same document chunked with and without it
    # has the same chunk cores. The hash is over the core: a passage that sits after a
    # different neighbour in another document still hashes the same.
    with_overlap = _chunks(overlap=20, intro="Lead-in sentence.")
    without = _chunks(overlap=0, intro="Lead-in sentence.")
    assert [text_hash(c.core_text) for c in with_overlap if not c.is_parent] == [
        text_hash(c.text) for c in without if not c.is_parent
    ]
    overlapped = [c for c in with_overlap if c.text != c.core_text]
    assert overlapped, "the fixture must produce overlap"
    for c in overlapped:
        assert text_hash(c.core_text) != text_hash(c.text)
        assert c.text.endswith(c.core_text)


def test_a_chunk_without_overlap_has_core_text_equal_to_text() -> None:
    chunks = _chunks(overlap=0, intro="Lead-in.")
    assert chunks and all(c.core_text == c.text for c in chunks)
