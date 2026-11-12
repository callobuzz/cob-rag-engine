"""OCR languages: RAG_OCR_LANGS parsing, engine code mapping, config, pipeline options."""

import logging

import numpy
import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.parsing import ocr_langs
from callobuzz_rag.parsing.ocr_langs import (
    LANGUAGES,
    ocr_config_advice,
    paddle_lang,
    parse_ocr_langs,
    rapidocr_langs,
    tesseract_codes,
    warn_ocr_config,
)

# --- parsing ---


def test_default_is_english() -> None:
    assert [lang.code for lang in parse_ocr_langs(ocr_langs.DEFAULT_OCR_LANGS)] == ["en"]


def test_parse_keeps_order_and_drops_duplicates_and_spaces() -> None:
    assert [lang.code for lang in parse_ocr_langs(" hi , EN,mr,hi ")] == ["hi", "en", "mr"]


@pytest.mark.parametrize("raw", ["", " , ", "xx", "hi,klingon"])
def test_parse_rejects_empty_and_unknown(raw: str) -> None:
    with pytest.raises(ValueError, match="RAG_OCR_LANGS"):
        parse_ocr_langs(raw)


def test_every_language_has_a_tesseract_code() -> None:
    assert all(lang.tesseract for lang in LANGUAGES.values())


# --- engine mapping ---


def test_tesseract_codes_for_hindi_and_english() -> None:
    assert tesseract_codes(parse_ocr_langs("hi,en")) == ["hin", "eng"]


def test_paddle_uses_first_non_english_language() -> None:
    assert paddle_lang(parse_ocr_langs("en,hi")) == "hi"
    assert paddle_lang(parse_ocr_langs("en")) == "en"
    assert paddle_lang(parse_ocr_langs("hi,en,mr")) == "hi"


def test_paddle_has_no_model_for_bengali() -> None:
    with pytest.raises(ValueError, match="paddleocr has no model for Bengali"):
        paddle_lang(parse_ocr_langs("bn"))


def test_rapidocr_english_keeps_the_bundled_models() -> None:
    assert rapidocr_langs(parse_ocr_langs("en")) is None


def test_rapidocr_hindi_names_the_devanagari_recognizer() -> None:
    assert rapidocr_langs(parse_ocr_langs("hi,en")) == ["devanagari"]


def test_rapidocr_has_no_model_for_gujarati() -> None:
    with pytest.raises(ValueError, match="rapidocr has no model for Gujarati"):
        rapidocr_langs(parse_ocr_langs("gu"))


def test_rapidocr_codes_are_ones_docling_resolves() -> None:
    """The native codes we hand Docling must exist in its PP-OCR vocabulary."""
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.pipeline_options import RapidOcrOptions
    from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel

    for code in ("hi", "mr", "ta", "te", "kn"):
        lang = rapidocr_langs(parse_ocr_langs(code))
        assert lang is not None
        model = RapidOcrModel(
            enabled=False,
            artifacts_path=None,
            options=RapidOcrOptions(backend="onnxruntime", lang=lang),
            accelerator_options=AcceleratorOptions(),
        )
        assert model.resolve_ocr_languages() == [lang[0]]


# --- advice ---


def test_advice_warns_for_indic_on_rapidocr() -> None:
    advice = ocr_config_advice("rapidocr", parse_ocr_langs("hi,en"))
    assert advice is not None
    assert "paddleocr" in advice
    assert "tesseract" in advice
    assert "Hindi" in advice


@pytest.mark.parametrize(
    ("engine", "langs"),
    [("rapidocr", "en"), ("paddleocr", "hi,en"), ("tesseract", "hi"), ("off", "hi")],
)
def test_no_advice_when_the_pairing_is_fine(engine: str, langs: str) -> None:
    assert ocr_config_advice(engine, parse_ocr_langs(langs)) is None


def test_warn_logs_once_and_ignores_bad_codes(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=ocr_langs.logger.name):
        warn_ocr_config("rapidocr", "hi,en")
        warn_ocr_config("rapidocr", "nonsense")
    assert [r.getMessage() for r in caplog.records].count(
        ocr_config_advice("rapidocr", parse_ocr_langs("hi,en"))
    ) == 1


# --- settings ---


def test_settings_default_and_normalisation() -> None:
    assert Settings().ocr_langs == "en"
    assert Settings(ocr_langs=" HI , en ").ocr_langs == "hi,en"
    assert Settings(ocr_engine=" PaddleOCR ").ocr_engine == "paddleocr"


def test_settings_reject_unknown_values() -> None:
    with pytest.raises(ValueError, match="RAG_OCR_LANGS"):
        Settings(ocr_langs="hi,zz")
    with pytest.raises(ValueError, match="RAG_OCR_ENGINE"):
        Settings(ocr_engine="abbyy")


# --- Docling pipeline options ---


def test_pipeline_options_per_engine() -> None:
    from callobuzz_rag.parsing.docling_parser import _pdf_pipeline_options

    english = _pdf_pipeline_options("rapidocr", "en")
    assert english.ocr_options.kind == "rapidocr"
    assert english.ocr_options.lang == ["ch"]  # Docling's default: the bundled models

    hindi = _pdf_pipeline_options("rapidocr", "hi,en")
    assert hindi.ocr_options.lang == ["devanagari"]

    tess = _pdf_pipeline_options("tesseract", "hi,en")
    assert type(tess.ocr_options).__name__ == "TesseractCliOcrOptions"
    assert tess.ocr_options.lang == ["hin", "eng"]

    assert _pdf_pipeline_options("off", "hi").do_ocr is False
    with pytest.raises(ValueError, match="paddleocr"):
        _pdf_pipeline_options("abbyy", "en")


def test_paddleocr_engine_names_the_extra_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.parsing import paddle_ocr
    from callobuzz_rag.parsing.docling_parser import _pdf_pipeline_options

    monkeypatch.setattr(paddle_ocr.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(paddle_ocr.OcrEngineUnavailableError, match=r"\[ocr-paddle\]"):
        _pdf_pipeline_options("paddleocr", "hi,en")


def test_paddleocr_options_when_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.parsing import paddle_ocr
    from callobuzz_rag.parsing.docling_parser import _pdf_pipeline_options

    monkeypatch.setattr(paddle_ocr, "require_paddleocr", lambda: None)
    options = _pdf_pipeline_options("paddleocr", "hi,en")
    assert options.ocr_options.kind == "paddleocr"
    assert options.ocr_options.lang == ["hi"]
    assert options.allow_external_plugins is True


# --- the PaddleOCR Docling model, with a fake reader ---


class _FakePaddle:
    def __init__(self) -> None:
        self.seen_shape: tuple[int, ...] | None = None

    def predict(self, image: numpy.ndarray) -> list[dict[str, object]]:
        self.seen_shape = image.shape
        return [
            {
                "rec_texts": ["धनवापसी", "  ", "low"],
                "rec_scores": [0.98, 0.99, 0.2],
                "rec_boxes": numpy.array([[10, 20, 110, 60], [0, 0, 1, 1], [5, 5, 9, 9]]),
            }
        ]


def test_paddle_model_filters_blank_and_low_confidence_lines() -> None:
    from docling.datamodel.accelerator_options import AcceleratorOptions

    from callobuzz_rag.parsing.paddle_ocr import PaddleOcrModel, PaddleOcrOptions

    model = PaddleOcrModel(
        enabled=False,
        artifacts_path=None,
        options=PaddleOcrOptions(lang=["hi"]),
        accelerator_options=AcceleratorOptions(),
    )
    fake = _FakePaddle()
    model.reader = fake
    rgb = numpy.zeros((4, 6, 3), dtype=numpy.uint8)
    rgb[:, :, 0] = 255  # red in RGB
    lines = model._read(rgb)
    assert lines == [((10.0, 20.0, 110.0, 60.0), "धनवापसी", pytest.approx(0.98))]
    assert fake.seen_shape == (4, 6, 3)


def test_paddle_model_is_registered_as_a_docling_plugin() -> None:
    from docling.models.factories import get_ocr_factory

    from callobuzz_rag.parsing.paddle_ocr import PaddleOcrModel, PaddleOcrOptions

    factory = get_ocr_factory(allow_external_plugins=True)
    assert factory._classes[PaddleOcrOptions] is PaddleOcrModel
    assert PaddleOcrOptions.kind == "paddleocr"


def test_reading_order_keeps_words_of_one_line_together() -> None:
    """Devanagari word boxes of one line start at different heights; they stay in x order."""
    from callobuzz_rag.parsing.paddle_ocr import reading_order

    line_one = [
        ((300.0, 40.0, 420.0, 110.0), "दो", 0.9),
        ((10.0, 20.0, 200.0, 95.0), "एक", 0.9),
        ((520.0, 32.0, 640.0, 100.0), "तीन", 0.9),
    ]
    line_two = [((10.0, 160.0, 300.0, 230.0), "चार", 0.9)]
    ordered = reading_order(line_two + line_one)
    assert [text for _, text, _ in ordered] == ["एक", "दो", "तीन", "चार"]
    assert reading_order([]) == []
