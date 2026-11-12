"""Answer-model routing by reply language (RAG_LLM_LANGUAGE_MODELS, llm_config.language_models).

The director detects the visitor's language before it answers (`conversation/language.py`,
rules only). A map from that language code to a model id lets one knowledge base answer
English with one model and Hindi or Hinglish with another: in the answer benchmark
(`benchmarks/README.md`) Gemma 4 31B had the best Hindi and Hinglish tone and the fastest
first word, gpt-oss-120b the best English.

Precedence, highest first: X-LLM-* headers (an API caller picked the model) -> the knowledge
base's `language_models[lang]` -> the knowledge base's `model` -> the engine's
`RAG_LLM_LANGUAGE_MODELS[lang]` -> the engine's default model. A knowledge base sending
`language_models: {}` switches routing off for itself; `null` leaves it to the engine.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Every code the language detector can emit (`conversation.types.LangKey`).
LANGUAGE_CODES: tuple[str, ...] = (
    "en",
    "hi",
    "hi-Latn",
    "mr",
    "ta",
    "te",
    "bn",
    "gu",
    "kn",
    "ml",
    "pa",
)
_CANONICAL = {code.lower(): code for code in LANGUAGE_CODES}
MAX_MODEL_LENGTH = 200

# The thinking level a routed model is asked for. "off" is the lowest setting every family
# understands: `llm/thinking.py` turns it into the model's own off switch, or, for a family that
# cannot switch thinking off (gpt-oss), into its minimum ("low").
ROUTED_THINKING = "off"


def clean_language_models(value: object) -> dict[str, str]:
    """A language-to-model map from a dict or the env var's JSON text. None and "" are {}.

    Raises ValueError on a bad shape, an unknown language code or an empty model id.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                'expected a JSON object such as {"hi": "google/gemma-4-31b-it"}'
            ) from exc
    if not isinstance(value, dict):
        raise ValueError("expected an object mapping a language code to a model id")
    out: dict[str, str] = {}
    for key, model in value.items():
        code = _CANONICAL.get(str(key).strip().lower())
        if code is None:
            raise ValueError(
                f"unknown language code {key!r}; use one of {', '.join(LANGUAGE_CODES)}"
            )
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"the model for {code} must be a non-empty string")
        if len(model.strip()) > MAX_MODEL_LENGTH:
            raise ValueError(f"the model for {code} is longer than {MAX_MODEL_LENGTH} characters")
        out[code] = model.strip()
    return out


def resolve_language_models(
    *,
    engine: dict[str, str],
    corpus: dict[str, Any],
    header_model: bool,
    same_endpoint: bool,
) -> dict[str, str]:
    """The language-to-model map that applies to one request.

    `header_model`: the caller sent X-LLM-Model, so nothing is routed. `same_endpoint`: the
    request resolved to the engine's own provider and base URL, the only place an engine-level
    model id (written for that provider) can be reached. A knowledge base's own entries are the
    owner's choice for the provider they configured and are kept.
    """
    if header_model:
        return {}
    stored = corpus.get("language_models")
    kb: dict[str, str] = {}
    if stored is not None:
        try:
            kb = clean_language_models(stored)
        except ValueError as exc:
            logger.warning("Ignoring invalid corpus language_models: %s", exc)
            stored = None
    if stored is not None and not kb:
        return {}  # {}: this knowledge base turned routing off
    inherited: dict[str, str] = {}
    if engine and corpus.get("model") is not None:
        pass  # the knowledge base picked its own model: it beats the engine's map
    elif engine and not same_endpoint:
        logger.info(
            "Engine language routing skipped: this request uses another provider or endpoint "
            "than the engine default, and %s name models of the engine's provider",
            "RAG_LLM_LANGUAGE_MODELS",
        )
    else:
        inherited = engine
    return {**inherited, **kb}


def routing_note(lang: str, model: str) -> str:
    """The owner trace's line for a routed answer."""
    return f"model chosen for {lang} by language routing: {model}"


def route_answer_llm(
    llm: Any, language_models: dict[str, str], text: str
) -> tuple[Any, str | None]:
    """The provider that writes the answer to `text`, and the routed model id (None = unrouted).

    For the surfaces that answer without the director (`/query`, `/query/stream`, widget chat
    and conversation messages with the assistant off). The language is the director's rules-only
    detector's (`conversation/language.py`) on the visitor's message. A routed answer runs on a
    copy of `llm` (`LiteLLMProvider.for_model`) that shares its cost ledger, at `ROUTED_THINKING`;
    a provider without `for_model` is returned as it is.
    """
    if not language_models:
        return llm, None
    from callobuzz_rag.conversation.language import detect_language

    model = language_models.get(detect_language(text).code)
    for_model = getattr(llm, "for_model", None)
    if not model or not callable(for_model):
        return llm, None
    return for_model(model, ROUTED_THINKING), model
