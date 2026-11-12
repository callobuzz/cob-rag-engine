"""Unit tests for document parser models and base interface."""

from pathlib import Path
from unittest.mock import patch

import pytest

from callobuzz_rag.parsing.errors import CorruptedFileError, ParsingError, UnsupportedFormatError
from callobuzz_rag.parsing.models import (
    ElementType,
    ParsedDocument,
    ParsedElement,
    TableData,
)

# --- Model tests ---


def test_element_type_values():
    """ElementType enum has expected members."""
    assert ElementType.HEADING == "heading"
    assert ElementType.PARAGRAPH == "paragraph"
    assert ElementType.TABLE == "table"
    assert ElementType.CODE == "code"
    assert ElementType.LIST_ITEM == "list_item"
    assert ElementType.IMAGE == "image"
    assert ElementType.UNKNOWN == "unknown"


def test_parsed_element_defaults():
    """ParsedElement has sensible defaults."""
    elem = ParsedElement(type=ElementType.PARAGRAPH, text="Hello")
    assert elem.page is None
    assert elem.level is None
    assert elem.table_data is None
    assert elem.metadata == {}


def test_table_data_creation():
    """TableData stores structured table info."""
    td = TableData(markdown="| A | B |", json_rows=[{"A": 1}], num_rows=1, num_cols=2)
    assert td.num_rows == 1
    assert td.num_cols == 2
    assert len(td.json_rows) == 1


def test_parsed_document_defaults():
    """ParsedDocument has sensible defaults."""
    doc = ParsedDocument(elements=[])
    assert doc.page_count is None
    assert doc.source_filename is None
    assert doc.parser_name == ""
    assert doc.warnings == []


# --- Error hierarchy tests ---


def test_parsing_error_hierarchy():
    """Parsing errors inherit correctly."""
    assert issubclass(UnsupportedFormatError, ParsingError)
    assert issubclass(CorruptedFileError, ParsingError)


# --- Docling parser tests (mocked) ---


async def test_unsupported_format_raises():
    """DoclingParserProvider rejects unknown MIME types."""
    from callobuzz_rag.parsing.docling_parser import DoclingParserProvider

    with patch("docling.document_converter.DocumentConverter"):
        provider = DoclingParserProvider()

    with pytest.raises(UnsupportedFormatError):
        await provider.parse(Path("/tmp/test.xyz"), mime_type="application/unknown")


def test_supported_formats_returns_mime_types():
    """supported_formats() returns known MIME types."""
    from callobuzz_rag.parsing.docling_parser import DoclingParserProvider

    with patch("docling.document_converter.DocumentConverter"):
        provider = DoclingParserProvider()

    formats = provider.supported_formats()
    assert "application/pdf" in formats
    assert "text/html" in formats
    assert "text/csv" in formats
    assert len(formats) == 11


class TestOcrEngine:
    """PDFs used to fail in the container: Docling fell back to RapidOCR's torch
    backend, which downloads weights into site-packages as a non-root user."""

    def test_default_is_rapidocr_on_onnxruntime(self) -> None:
        from docling.datamodel.pipeline_options import RapidOcrOptions

        from callobuzz_rag.parsing.docling_parser import _pdf_pipeline_options

        options = _pdf_pipeline_options("rapidocr")
        assert options.do_ocr is True
        assert isinstance(options.ocr_options, RapidOcrOptions)
        assert options.ocr_options.backend == "onnxruntime"

    def test_tesseract_and_off(self) -> None:
        from docling.datamodel.pipeline_options import TesseractCliOcrOptions

        from callobuzz_rag.parsing.docling_parser import _pdf_pipeline_options

        assert isinstance(_pdf_pipeline_options("tesseract").ocr_options, TesseractCliOcrOptions)
        assert _pdf_pipeline_options("off").do_ocr is False

    def test_unknown_engine_is_rejected(self) -> None:
        from callobuzz_rag.parsing.docling_parser import _pdf_pipeline_options

        with pytest.raises(ValueError, match="RAG_OCR_ENGINE"):
            _pdf_pipeline_options("abbyy")

    def test_spreadsheets_and_csv_are_supported(self) -> None:
        from callobuzz_rag.parsing.docling_parser import MIME_TO_FORMAT

        assert (
            MIME_TO_FORMAT["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]
            == "XLSX"
        )
        assert MIME_TO_FORMAT["text/csv"] == "CSV"
