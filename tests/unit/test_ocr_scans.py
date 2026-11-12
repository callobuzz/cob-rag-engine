"""OCR of a synthetic scanned Hindi page, per engine.

The page (tests/fixtures/ocr/scanned-hindi.pdf and .png) is Devanagari and English text drawn
with Windows' Nirmala UI, with no text layer. Each engine runs only where it is installed: an
engine that is missing skips cleanly. Accuracy is 1 - (edit distance / expected length) over the
text with all whitespace removed, so line breaks and spacing do not count against an engine.
"""

import asyncio
import json
import shutil
import time
import unicodedata
from pathlib import Path

import pytest

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.chunking.index_text import index_text
from callobuzz_rag.parsing.models import ParsedDocument
from callobuzz_rag.retrieval.sparse import analyze

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ocr"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
EXPECTED_TEXT = "\n".join(EXPECTED["lines"])
MIN_ACCURACY = 0.90


def _squash(text: str) -> str:
    return "".join(unicodedata.normalize("NFC", text).split())


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def accuracy(actual: str, expected: str = EXPECTED_TEXT) -> float:
    want, got = _squash(expected), _squash(actual)
    return max(0.0, 1.0 - _edit_distance(got, want) / len(want))


def _installed(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def _ocr(engine: str, langs: str) -> tuple[ParsedDocument, float]:
    from callobuzz_rag.parsing.docling_parser import DoclingParserProvider

    parser = DoclingParserProvider(ocr_engine=engine, ocr_langs=langs)
    asyncio.run(parser.parse(FIXTURES / "scanned-hindi.pdf", "application/pdf"))  # warm-up
    started = time.perf_counter()
    doc = asyncio.run(parser.parse(FIXTURES / "scanned-hindi.pdf", "application/pdf"))
    return doc, time.perf_counter() - started


def _text(doc: ParsedDocument) -> str:
    return "\n".join(element.text for element in doc.elements)


# --- the fixture itself ---


def test_fixture_is_an_image_only_pdf() -> None:
    pdfium = pytest.importorskip("pypdfium2")
    page = pdfium.PdfDocument(str(FIXTURES / "scanned-hindi.pdf"))[0]
    assert page.get_textpage().count_chars() == 0


def test_accuracy_metric() -> None:
    assert accuracy(EXPECTED_TEXT) == 1.0
    assert accuracy(EXPECTED_TEXT.replace("दुकान", "")) < 0.99
    assert accuracy("") == 0.0


# --- the Indic tokenizer and chunker on Hindi text (no OCR needed) ---


def test_hindi_words_survive_the_sparse_tokenizer_whole() -> None:
    terms = analyze(EXPECTED_TEXT)
    for word in EXPECTED["hindi_words"]:
        assert word in terms, f"{word} was split or dropped by the tokenizer: {terms}"
    assert "refund" in terms  # Latin words still stem normally


def test_hindi_text_chunks_on_the_danda() -> None:
    from callobuzz_rag.parsing.models import ElementType, ParsedElement

    long_hindi = " ".join(EXPECTED["lines"][1:2] * 120)
    doc = ParsedDocument(elements=[ParsedElement(type=ElementType.PARAGRAPH, text=long_hindi)])
    chunks = StructureAwareChunker().chunk(doc)
    assert len(chunks) > 1
    assert all("।" in chunk.text for chunk in chunks)
    assert all(not chunk.text.startswith("हैं") for chunk in chunks)


# --- real engines ---


@pytest.mark.slow
def test_rapidocr_english_default_runs() -> None:
    pytest.importorskip("onnxruntime")
    doc, seconds = _ocr("rapidocr", "en")
    text = _text(doc)
    assert "Refund" in text
    assert "support@example.com" in text
    print(f"rapidocr en: {seconds:.1f}s, accuracy {accuracy(text):.2f}")


@pytest.mark.slow
def test_rapidocr_hindi_behaviour_is_documented() -> None:
    """RapidOCR's Devanagari recognizer reads some Hindi lines and garbles or skips others.

    This pins what we told operators: it works for short lines, not reliably for a page, so the
    startup warning recommends paddleocr or tesseract. If an upgrade makes it pass the bar, this
    test fails and the warning in parsing/ocr_langs.py should be revisited.
    """
    pytest.importorskip("onnxruntime")
    try:
        doc, seconds = _ocr("rapidocr", "hi,en")
    except Exception as exc:  # the recognizer is downloaded on first use; offline is fine here
        pytest.skip(f"RapidOCR Devanagari model unavailable: {exc}")
    text = _text(doc)
    score = accuracy(text)
    print(f"rapidocr hi,en: {seconds:.1f}s, accuracy {score:.2f}")
    assert any(word in text for word in EXPECTED["hindi_words"]), "no Hindi word recovered"
    assert score < MIN_ACCURACY


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not installed")
def test_tesseract_reads_hindi() -> None:
    doc, seconds = _ocr("tesseract", "hi,en")
    text = _text(doc)
    score = accuracy(text)
    print(f"tesseract hin+eng: {seconds:.1f}s, accuracy {score:.2f}")
    assert score >= MIN_ACCURACY


@pytest.mark.slow
@pytest.mark.skipif(not _installed("paddleocr"), reason="pip install '.[ocr-paddle]'")
def test_paddleocr_reads_hindi() -> None:
    doc, seconds = _ocr("paddleocr", "hi,en")
    text = _text(doc)
    score = accuracy(text)
    print(f"paddleocr hi: {seconds:.1f}s, accuracy {score:.2f}")
    assert score >= MIN_ACCURACY
    for word in EXPECTED["hindi_words"]:
        assert word in text


@pytest.mark.slow
@pytest.mark.skipif(not _installed("paddleocr"), reason="pip install '.[ocr-paddle]'")
def test_ocr_output_goes_through_chunker_and_tokenizer() -> None:
    doc, _ = _ocr("paddleocr", "hi,en")
    chunks = StructureAwareChunker().chunk(doc)
    assert chunks
    # what the keyword index sees: the heading path plus the chunk text
    terms = analyze(
        " ".join(
            index_text(chunk.text, chunk.structure_path, heading_context=True) for chunk in chunks
        )
    )
    for word in EXPECTED["hindi_words"]:
        assert word in terms
