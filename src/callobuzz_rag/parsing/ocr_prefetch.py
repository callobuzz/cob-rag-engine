"""Download OCR models at image build time, so a container needs no write access later.

    python -m callobuzz_rag.parsing.ocr_prefetch rapidocr hi,en
    python -m callobuzz_rag.parsing.ocr_prefetch paddleocr hi,en

RapidOCR bundles only its English models and fetches any other recognizer into its package
directory on first use; the container runs as a non-root user that cannot write there, so the
Dockerfile runs this as root. PaddleOCR fetches into `PADDLE_PDX_CACHE_HOME`. Exits 1 when the
download fails, so the Dockerfile can decide whether that is fatal.
"""

import logging
import sys

logger = logging.getLogger(__name__)


def prefetch(engine: str, langs: str) -> None:
    """Load the engine for `langs` once, which downloads whatever it is missing."""
    from docling.datamodel.accelerator_options import AcceleratorOptions

    from callobuzz_rag.parsing.docling_parser import _pdf_pipeline_options

    options = _pdf_pipeline_options(engine, langs).ocr_options
    accelerator = AcceleratorOptions()
    if engine == "rapidocr":
        from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel

        RapidOcrModel(
            enabled=True, artifacts_path=None, options=options, accelerator_options=accelerator
        )
    elif engine == "paddleocr":
        from callobuzz_rag.parsing.paddle_ocr import PaddleOcrModel

        PaddleOcrModel(
            enabled=True, artifacts_path=None, options=options, accelerator_options=accelerator
        )
    else:
        raise ValueError(f"nothing to prefetch for RAG_OCR_ENGINE={engine!r}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        logger.error("usage: python -m callobuzz_rag.parsing.ocr_prefetch ENGINE LANGS")
        return 2
    engine, langs = argv
    try:
        prefetch(engine, langs)
    except Exception as exc:
        logger.error("OCR model download failed for %s %s: %s", engine, langs, exc)
        return 1
    logger.info("OCR models ready for %s %s", engine, langs)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main(sys.argv[1:]))
