"""OCR languages: the `RAG_OCR_LANGS` list and each OCR engine's own codes.

The setting is one comma list of ISO 639-1 codes (`en`, `hi,en`, `hi,en,mr`). Order is
preference order: an engine that reads one language at a time (RapidOCR, PaddleOCR) uses the
first non-English one. The Devanagari recognizers read Latin text too, so `hi,en` needs one
recognizer, not two.

Engine coverage (checked against RapidOCR 3.9, PaddleOCR 3.7 and Tesseract 5 on 2026-10-04):

    RapidOCR   en (bundled), Devanagari (hi mr ne sa), ta, te, kn. Models are downloaded on first
               use, so the image fetches them at build time (`parsing/ocr_prefetch.py`).
    PaddleOCR  en, Devanagari (hi mr ne sa), ta, te, kn. The best Hindi accuracy we measured.
    Tesseract  every language below, from its `tesseract-ocr-<code>` data package.
"""

import logging
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

DEFAULT_OCR_LANGS: Final = "en"
OCR_ENGINES: Final = ("rapidocr", "tesseract", "paddleocr", "off")


@dataclass(frozen=True)
class OcrLanguage:
    """One language and the code each engine knows it by (None: that engine can't read it)."""

    code: str
    name: str
    script: str
    tesseract: str
    rapidocr: str | None = None
    paddleocr: str | None = None


_LANGUAGES: Final[tuple[OcrLanguage, ...]] = (
    OcrLanguage("en", "English", "Latin", "eng", rapidocr="en", paddleocr="en"),
    OcrLanguage("hi", "Hindi", "Devanagari", "hin", "devanagari", "hi"),
    OcrLanguage("mr", "Marathi", "Devanagari", "mar", "devanagari", "mr"),
    OcrLanguage("ne", "Nepali", "Devanagari", "nep", "devanagari", "ne"),
    OcrLanguage("sa", "Sanskrit", "Devanagari", "san", "devanagari", "sa"),
    OcrLanguage("ta", "Tamil", "Tamil", "tam", "ta", "ta"),
    OcrLanguage("te", "Telugu", "Telugu", "tel", "te", "te"),
    OcrLanguage("kn", "Kannada", "Kannada", "kan", "ka", "ka"),
    OcrLanguage("bn", "Bengali", "Bengali", "ben"),
    OcrLanguage("gu", "Gujarati", "Gujarati", "guj"),
    OcrLanguage("ml", "Malayalam", "Malayalam", "mal"),
    OcrLanguage("pa", "Punjabi", "Gurmukhi", "pan"),
    OcrLanguage("or", "Odia", "Odia", "ori"),
    OcrLanguage("as", "Assamese", "Bengali", "asm"),
    OcrLanguage("ur", "Urdu", "Arabic", "urd"),
)
LANGUAGES: Final[dict[str, OcrLanguage]] = {lang.code: lang for lang in _LANGUAGES}

# Everything except English and Urdu is an Indic-script language here.
_NON_INDIC: Final = frozenset({"en", "ur"})


def parse_ocr_langs(raw: str | list[str] | tuple[str, ...]) -> list[OcrLanguage]:
    """Parse `RAG_OCR_LANGS`. Raises ValueError on an empty list or an unknown code."""
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    codes = [item.strip().lower() for item in items if item.strip()]
    if not codes:
        raise ValueError("RAG_OCR_LANGS is empty: use a comma list such as 'en' or 'hi,en'")
    unknown = [code for code in codes if code not in LANGUAGES]
    if unknown:
        raise ValueError(
            f"RAG_OCR_LANGS has unknown language code(s) {unknown}; supported: "
            f"{', '.join(LANGUAGES)}"
        )
    return [LANGUAGES[code] for code in dict.fromkeys(codes)]


def is_indic(language: OcrLanguage) -> bool:
    return language.code not in _NON_INDIC


def has_indic(languages: list[OcrLanguage]) -> bool:
    return any(is_indic(language) for language in languages)


def primary_language(languages: list[OcrLanguage]) -> OcrLanguage:
    """The language a one-language-at-a-time engine loads: the first non-English one."""
    return next((lang for lang in languages if lang.code != "en"), languages[0])


def tesseract_codes(languages: list[OcrLanguage]) -> list[str]:
    """`hin+eng` style list for Tesseract, in the order given."""
    return [language.tesseract for language in languages]


def paddle_lang(languages: list[OcrLanguage]) -> str:
    """The `lang=` PaddleOCR loads. Raises ValueError when it has no model for the language."""
    primary = primary_language(languages)
    if primary.paddleocr is None:
        raise ValueError(
            f"RAG_OCR_ENGINE=paddleocr has no model for {primary.name} ({primary.code}); "
            "use tesseract for it"
        )
    return primary.paddleocr


def rapidocr_langs(languages: list[OcrLanguage]) -> list[str] | None:
    """Docling `RapidOcrOptions.lang` for the list, or None to keep the bundled default.

    English only keeps RapidOCR's bundled models (nothing to download). Any other language
    names its recognizer, which is fetched on first use.
    """
    primary = primary_language(languages)
    if primary.code == "en":
        return None
    if primary.rapidocr is None:
        raise ValueError(
            f"RAG_OCR_ENGINE=rapidocr has no model for {primary.name} ({primary.code}); "
            "use tesseract for it"
        )
    return [primary.rapidocr]


def ocr_config_advice(engine: str, languages: list[OcrLanguage]) -> str | None:
    """A one-line warning when the engine is a poor fit for the languages, else None."""
    engine = engine.lower()
    if engine == "off" or not has_indic(languages):
        return None
    names = ", ".join(language.name for language in languages if is_indic(language))
    if engine == "rapidocr":
        return (
            f"RAG_OCR_LANGS includes {names} but RAG_OCR_ENGINE=rapidocr reads Devanagari and "
            "other Indic scripts unreliably (long lines come out garbled or are skipped in our "
            "test scan). Set RAG_OCR_ENGINE=paddleocr (best accuracy, `pip install "
            "'cob-rag-engine[ocr-paddle]'`) or tesseract (needs the `tesseract-ocr-hin` style "
            "data packages)."
        )
    return None


def warn_ocr_config(engine: str, raw_langs: str) -> None:
    """Log the startup warning for a poor engine / language pairing. Never raises."""
    try:
        advice = ocr_config_advice(engine, parse_ocr_langs(raw_langs))
    except ValueError:
        return  # config validation reports bad codes; this is only advice
    if advice:
        logger.warning(advice)
