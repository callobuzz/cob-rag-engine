"""PaddleOCR as a Docling OCR engine (`RAG_OCR_ENGINE=paddleocr`).

Docling has no PaddleOCR backend of its own (RapidOCR runs the same PP-OCR models on ONNX, but
its Devanagari path is unreliable, see `ocr_langs.py`), so this registers one through Docling's
plugin interface: `PaddleOcrOptions` is the options type, `PaddleOcrModel` the engine, and
`ocr_engines()` the entry point listed under `[project.entry-points.docling]` in pyproject.toml.
Docling loads third-party plugins only when `PdfPipelineOptions.allow_external_plugins` is set,
which `docling_parser._pdf_pipeline_options` does for this engine.

PaddleOCR is an optional extra (`pip install 'cob-rag-engine[ocr-paddle]'`, Apache-2.0) and is
imported only when the engine is built.
"""

import importlib.util
import logging
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, ClassVar

import numpy
from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.base_models import Page
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import OcrOptions
from docling.datamodel.settings import settings
from docling.models.base_ocr_model import BaseOcrModel
from docling_core.types.doc import BoundingBox, CoordOrigin
from docling_core.types.doc.page import BoundingRectangle, TextCell
from pydantic import Field

from callobuzz_rag.parsing.errors import ParsingError

logger = logging.getLogger(__name__)

INSTALL_HINT = (
    "RAG_OCR_ENGINE=paddleocr needs PaddleOCR, which is an optional extra: "
    "pip install 'cob-rag-engine[ocr-paddle]' (PaddleOCR + PaddlePaddle, both Apache-2.0)"
)


class OcrEngineUnavailableError(ParsingError):
    """The configured OCR engine is not installed."""


def require_paddleocr() -> None:
    """Raise OcrEngineUnavailableError with the install command when PaddleOCR is missing."""
    if importlib.util.find_spec("paddleocr") is None or importlib.util.find_spec("paddle") is None:
        raise OcrEngineUnavailableError(INSTALL_HINT)


class PaddleOcrOptions(OcrOptions):  # type: ignore[misc]  # docling is untyped
    """Options for the PaddleOCR engine. `lang` holds PaddleOCR codes (`hi`, `en`, `ta`...)."""

    kind: ClassVar[str] = "paddleocr"
    # PaddleOCR's own codes go to it untouched; Docling's BCP-47 canonicalizer is not involved.
    canonicalize_lang: ClassVar[bool] = False

    lang: Annotated[
        list[str],
        Field(
            default_factory=lambda: ["en"],
            description="PaddleOCR language codes, the first one is used",
        ),
    ]
    text_detection_model_name: Annotated[
        str | None,
        Field(
            description=(
                "Detector to load instead of the language default (the server detector). "
                "PaddleOCR then also needs text_recognition_model_name: it picks the "
                "recognizer from `lang` only when neither name is given."
            )
        ),
    ] = None
    text_recognition_model_name: Annotated[
        str | None, Field(description="Recognizer to load, e.g. devanagari_PP-OCRv5_mobile_rec")
    ] = None
    scale: Annotated[float, Field(description="Page render scale (72 dpi x scale)")] = 2.0
    fast: Annotated[
        bool,
        Field(
            description=(
                "Devanagari only: pair the mobile detector with the mobile recognizer. On "
                "the test scan that is 4x faster than PaddleOCR's server detector at the "
                "same accuracy (99.4% vs 99.7%, 5.5 s vs 26 s a page on CPU)."
            )
        ),
    ] = True
    # oneDNN in PaddlePaddle 3.x CPU builds fails on the PP-OCR detector with
    # "ConvertPirAttribute2RuntimeAttribute not support" (observed with 3.3.1), so it is off.
    enable_mkldnn: Annotated[bool, Field(description="Use oneDNN on CPU")] = False
    text_score: Annotated[float, Field(description="Drop lines below this confidence")] = 0.5


DEVANAGARI_CODES = frozenset({"hi", "mr", "ne", "sa"})

Line = tuple[tuple[float, float, float, float], str, float]


def reading_order(lines: list[Line]) -> list[Line]:
    """Top-to-bottom, left-to-right. PaddleOCR's own sort groups boxes into a row only when
    their tops are within 10 px, which a Devanagari headline (the shirorekha sits at a
    different height per word) breaks at Docling's 3x scale, scrambling the words of a line."""
    if len(lines) < 2:
        return lines
    heights = sorted(box[3] - box[1] for box, _, _ in lines)
    row_gap = max(heights[len(heights) // 2] * 0.5, 1.0)
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda item: (item[0][1] + item[0][3]) / 2):
        centre = (line[0][1] + line[0][3]) / 2
        if rows:
            centres = [(m[0][1] + m[0][3]) / 2 for m in rows[-1]]
            if abs(centre - sum(centres) / len(centres)) <= row_gap:
                rows[-1].append(line)
                continue
        rows.append([line])
    return [line for row in rows for line in sorted(row, key=lambda item: item[0][0])]


class PaddleOcrModel(BaseOcrModel):  # type: ignore[misc]  # docling is untyped
    """Docling OCR stage backed by a PaddleOCR pipeline (detector + recognizer)."""

    def __init__(
        self,
        *,
        enabled: bool,
        artifacts_path: Path | None,
        options: PaddleOcrOptions,
        accelerator_options: AcceleratorOptions,
    ):
        super().__init__(
            enabled=enabled,
            artifacts_path=artifacts_path,
            options=options,
            accelerator_options=accelerator_options,
        )
        self.options: PaddleOcrOptions
        self.scale = options.scale
        self._lock = threading.Lock()  # one predictor, shared by the worker's page threads
        self.reader: Any = None
        if enabled:
            self.reader = self._load_reader()

    def _load_reader(self) -> Any:
        require_paddleocr()
        from paddleocr import PaddleOCR  # deferred: heavy, optional

        kwargs: dict[str, Any] = {
            "lang": self.options.lang[0] if self.options.lang else "en",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "enable_mkldnn": self.options.enable_mkldnn,
        }
        detector = self.options.text_detection_model_name
        recognizer = self.options.text_recognition_model_name
        fast_pair = self.options.fast and detector is None and recognizer is None
        if fast_pair and kwargs["lang"] in DEVANAGARI_CODES:
            detector, recognizer = "PP-OCRv5_mobile_det", "devanagari_PP-OCRv5_mobile_rec"
        if detector:
            kwargs["text_detection_model_name"] = detector
        if recognizer:
            kwargs["text_recognition_model_name"] = recognizer
        return PaddleOCR(**kwargs)

    def _read(self, image: numpy.ndarray) -> list[Line]:
        """OCR one RGB page crop: (x0, y0, x1, y1, in crop pixels), text, confidence per line."""
        bgr = numpy.ascontiguousarray(image[:, :, ::-1])  # PaddleOCR reads OpenCV BGR
        with self._lock:
            results = self.reader.predict(bgr)
        lines: list[Line] = []
        for result in results:
            texts = result["rec_texts"]
            scores = result["rec_scores"]
            boxes = result["rec_boxes"]
            for text, score, box in zip(texts, scores, boxes, strict=False):
                if not text.strip() or score < self.options.text_score:
                    continue
                x0, y0, x1, y1 = (float(v) for v in box)
                lines.append(((x0, y0, x1, y1), text, float(score)))
        return reading_order(lines)

    def __call__(self, conv_res: ConversionResult, page_batch: Iterable[Page]) -> Iterable[Page]:
        if not self.enabled:
            yield from page_batch
            return

        for page in page_batch:
            assert page._backend is not None
            if not page._backend.is_valid():
                yield page
                continue

            ocr_rects = self.get_ocr_rects(page)
            all_cells: list[TextCell] = []
            for ocr_rect in ocr_rects:
                if ocr_rect.area() == 0:
                    continue
                crop = page._backend.get_page_image(scale=self.scale, cropbox=ocr_rect)
                lines = self._read(numpy.array(crop))
                for index, ((x0, y0, x1, y1), text, score) in enumerate(lines):
                    all_cells.append(
                        TextCell(
                            index=len(all_cells) + index,
                            text=text,
                            orig=text,
                            confidence=score,
                            from_ocr=True,
                            rect=BoundingRectangle.from_bounding_box(
                                BoundingBox.from_tuple(
                                    coord=(
                                        x0 / self.scale + ocr_rect.l,
                                        y0 / self.scale + ocr_rect.t,
                                        x1 / self.scale + ocr_rect.l,
                                        y1 / self.scale + ocr_rect.t,
                                    ),
                                    origin=CoordOrigin.TOPLEFT,
                                )
                            ),
                        )
                    )

            self.post_process_cells(all_cells, page, conv_res)
            if settings.debug.visualize_ocr:
                self.draw_ocr_rects_and_cells(conv_res, page, ocr_rects)
            yield page

    @classmethod
    def get_options_type(cls) -> type[OcrOptions]:
        return PaddleOcrOptions


def ocr_engines() -> dict[str, list[type[BaseOcrModel]]]:
    """Docling plugin hook (entry point group `docling`)."""
    return {"ocr_engines": [PaddleOcrModel]}
