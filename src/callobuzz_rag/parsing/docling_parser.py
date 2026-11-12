"""Docling-based document parser implementation.

Uses IBM Docling for PDF, DOCX, PPTX, XLSX, CSV, HTML, Markdown and image parsing.
Maps Docling's internal document model to our ParsedElement model.

Verified against docling==2.130.0 (the HTML furniture rules below are version-specific:
re-check `HTMLBackendOptions.infer_furniture` and the pre-first-heading rule on an upgrade).
"""

import asyncio
import importlib.metadata
import logging
import tempfile
from pathlib import Path
from typing import Any

from callobuzz_rag.parsing.base import DocumentParserProvider
from callobuzz_rag.parsing.errors import CorruptedFileError, UnsupportedFormatError
from callobuzz_rag.parsing.html_prestrip import prestrip_html
from callobuzz_rag.parsing.models import (
    ElementType,
    ParsedDocument,
    ParsedElement,
    TableData,
)
from callobuzz_rag.parsing.ocr_langs import (
    DEFAULT_OCR_LANGS,
    paddle_lang,
    parse_ocr_langs,
    rapidocr_langs,
    tesseract_codes,
)

logger = logging.getLogger(__name__)

# MIME type to Docling InputFormat mapping
MIME_TO_FORMAT: dict[str, str] = {
    "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
    "text/csv": "CSV",
    "text/html": "HTML",
    "text/markdown": "MD",
    "text/plain": "MD",
    "image/png": "IMAGE",
    "image/jpeg": "IMAGE",
    "image/tiff": "IMAGE",
}


def _pdf_pipeline_options(ocr_engine: str, ocr_langs: str = DEFAULT_OCR_LANGS) -> Any:
    """PDF/image pipeline with an explicitly chosen OCR engine and languages.

    Left to itself, Docling picks RapidOCR and — with torch installed but not
    onnxruntime — its torch backend, which DOWNLOADS weights into site-packages on
    the first PDF. The container runs as a non-root user, so every PDF failed with
    `Permission denied: .../rapidocr/models/PP-OCRv6_det_small.pth`. Naming the
    engine and backend here uses the ONNX models already shipped in the package.

    rapidocr (default) — RapidOCR on onnxruntime. English uses the bundled models; other
                         languages use recognizers fetched at image build time
                         (`parsing/ocr_prefetch.py`).
    tesseract          — the tesseract binary, with one traineddata package per language.
    paddleocr          — PaddleOCR (optional extra), through `parsing/paddle_ocr.py`.
    off                — no OCR: digital text only; scanned pages come out empty.

    `ocr_langs` is the `RAG_OCR_LANGS` comma list (`parsing/ocr_langs.py`).
    """
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        RapidOcrOptions,
        TesseractCliOcrOptions,
    )

    options = PdfPipelineOptions()
    engine = ocr_engine.lower()
    languages = parse_ocr_langs(ocr_langs)
    if engine == "off":
        options.do_ocr = False
    elif engine == "tesseract":
        options.ocr_options = TesseractCliOcrOptions(lang=tesseract_codes(languages))
    elif engine == "rapidocr":
        rapid_langs = rapidocr_langs(languages)
        if rapid_langs is None:
            options.ocr_options = RapidOcrOptions(backend="onnxruntime")
        else:
            options.ocr_options = RapidOcrOptions(backend="onnxruntime", lang=rapid_langs)
    elif engine == "paddleocr":
        from callobuzz_rag.parsing.paddle_ocr import PaddleOcrOptions, require_paddleocr

        require_paddleocr()
        options.ocr_options = PaddleOcrOptions(lang=[paddle_lang(languages)])
        options.allow_external_plugins = True
    else:
        raise ValueError(
            f"RAG_OCR_ENGINE={ocr_engine!r}: use rapidocr, tesseract, paddleocr or off"
        )
    return options


class DoclingParserProvider(DocumentParserProvider):
    """Docling-based document parser."""

    def __init__(self, ocr_engine: str = "rapidocr", ocr_langs: str = DEFAULT_OCR_LANGS) -> None:
        # Lazy import — Docling is heavy, don't load until needed
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import (
            DocumentConverter,
            ImageFormatOption,
            PdfFormatOption,
        )

        pdf_options = _pdf_pipeline_options(ocr_engine, ocr_langs)
        self._converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=pdf_options),
            }
        )
        self._prestripped_html_converter: Any = None

    def _html_converter_for_stripped_pages(self) -> Any:
        """A converter that keeps everything in a page whose chrome was already removed.

        Docling treats text before a page's first heading as furniture and drops it, which
        loses real lead paragraphs. After `prestrip_html` the chrome is gone, so nothing in
        what is left should be dropped: `infer_furniture=False` keeps it all.
        """
        if self._prestripped_html_converter is None:
            from docling.datamodel.backend_options import HTMLBackendOptions
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import DocumentConverter, HTMLFormatOption

            self._prestripped_html_converter = DocumentConverter(
                allowed_formats=[InputFormat.HTML],
                format_options={
                    InputFormat.HTML: HTMLFormatOption(
                        backend_options=HTMLBackendOptions(infer_furniture=False)
                    )
                },
            )
        return self._prestripped_html_converter

    async def parse(
        self, file_path: Path, mime_type: str | None = None, *, strip_chrome: bool = True
    ) -> ParsedDocument:
        """Parse document using Docling. Runs in thread pool (Docling is sync/CPU-heavy).

        An HTML page is first stripped of its chrome (`parsing/html_prestrip.py`) unless
        `strip_chrome` is False or the page is nothing but chrome.
        """
        if mime_type and mime_type not in MIME_TO_FORMAT:
            raise UnsupportedFormatError(f"Unsupported MIME type: {mime_type}")

        try:
            if strip_chrome and mime_type == "text/html":
                result = await asyncio.to_thread(self._convert_html, file_path)
            else:
                result = await asyncio.to_thread(self._converter.convert, str(file_path))
        except Exception as e:
            logger.error("Docling parsing failed for %s: %s", file_path, e)
            raise CorruptedFileError(f"Failed to parse {file_path.name}: {e}") from e

        return self._map_to_parsed_document(result, file_path, mime_type)

    def _convert_html(self, file_path: Path) -> Any:
        """Convert an HTML file, without its page chrome when there is some to remove."""
        stripped = prestrip_html(file_path.read_bytes())
        if stripped is None:
            return self._converter.convert(str(file_path))
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
            tmp.write(stripped)
            tmp_path = Path(tmp.name)
        try:
            return self._html_converter_for_stripped_pages().convert(str(tmp_path))
        finally:
            tmp_path.unlink(missing_ok=True)

    def supported_formats(self) -> list[str]:
        return list(MIME_TO_FORMAT.keys())

    def _map_to_parsed_document(
        self, result: Any, file_path: Path, mime_type: str | None
    ) -> ParsedDocument:
        """Map Docling ConversionResult to our ParsedDocument model."""
        from docling_core.types.doc import DocItemLabel

        doc = result.document
        elements: list[ParsedElement] = []
        warnings: list[str] = []

        # Map DocItemLabel to our ElementType
        label_map = {
            DocItemLabel.TITLE: ElementType.HEADING,
            DocItemLabel.SECTION_HEADER: ElementType.HEADING,
            DocItemLabel.PARAGRAPH: ElementType.PARAGRAPH,
            DocItemLabel.TEXT: ElementType.PARAGRAPH,
            DocItemLabel.TABLE: ElementType.TABLE,
            DocItemLabel.CODE: ElementType.CODE,
            DocItemLabel.LIST_ITEM: ElementType.LIST_ITEM,
            DocItemLabel.CAPTION: ElementType.CAPTION,
            DocItemLabel.FORMULA: ElementType.FORMULA,
            DocItemLabel.PICTURE: ElementType.IMAGE,
        }

        try:
            for item, level in doc.iterate_items():
                elem_type = label_map.get(item.label, ElementType.UNKNOWN)

                # Extract page number from provenance
                page_no: int | None = None
                if hasattr(item, "prov") and item.prov:
                    page_no = item.prov[0].page_no

                element = ParsedElement(
                    type=elem_type,
                    text=getattr(item, "text", "") or "",
                    page=page_no,
                    level=level if elem_type == ElementType.HEADING else None,
                )

                # Special handling for tables
                if elem_type == ElementType.TABLE:
                    try:
                        table_md = item.export_to_markdown(doc=doc)
                        json_rows: list[dict[str, Any]] = []
                        try:
                            df = item.export_to_dataframe(doc=doc)
                            json_rows = df.to_dict(orient="records")
                        except Exception:
                            pass  # DataFrame export is best-effort

                        element.table_data = TableData(
                            markdown=table_md,
                            json_rows=json_rows,
                            num_rows=item.data.num_rows,
                            num_cols=item.data.num_cols,
                        )
                        element.text = table_md
                    except Exception as e:
                        warnings.append(f"Table export failed: {e}")
                        element.text = str(item)

                if element.text:  # Skip empty elements
                    elements.append(element)

        except Exception as e:
            logger.warning("Error iterating Docling document: %s", e)
            warnings.append(f"Partial parse — iteration error: {e}")

        page_count = doc.num_pages() or None

        return ParsedDocument(
            elements=elements,
            page_count=page_count if page_count else None,
            source_filename=file_path.name,
            mime_type=mime_type,
            parser_name="docling",
            parser_version=importlib.metadata.version("docling"),
            warnings=warnings,
        )
