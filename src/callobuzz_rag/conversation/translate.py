"""Translated retrieval variant (design 4.3): Hindi/Hinglish questions over English documents.

One fast-model call returning {"query": "..."}; cached; any failure (no credential, timeout,
bad JSON, an echo of the input) returns None and the original query is searched alone.

The call must go through the same per-request `LLMProvider` that answers, so its cost lands
on the request's cost ledger (`llm/cost.py`); a cache hit makes no call and costs nothing.
"""

import asyncio
import hashlib
import json
import logging
import unicodedata

from callobuzz_rag.conversation.cache import RedisJsonCache
from callobuzz_rag.conversation.types import LangKey, LanguageInfo
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

logger = logging.getLogger(__name__)
TRANSLATE_CACHE_TTL_S = 604800
TRANSLATE_CACHE_PREFIX = "atrans:v1:"
_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "hi-Latn": "Hindi written in Roman letters (Hinglish)",
    "mr": "Marathi",
    "ta": "Tamil",
    "te": "Telugu",
    "bn": "Bengali",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
}

PROMPT = """Rewrite this customer question as a short search query in {target}, \
for searching a business's documents.
Keep names, places, product names and numbers exactly as written. Do not answer the question.

Question: {question}

Respond with ONLY this JSON: {{"query": "<the search query>"}}"""


def _normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).lower().split())


async def translate_for_retrieval(
    llm: LLMProvider,
    text: str,
    info: LanguageInfo,
    target: LangKey,
    *,
    cache: RedisJsonCache | None,
    timeout_s: float,
) -> str | None:
    """A search query in `target` for `text`, or None when no variant is needed or possible."""
    if info.code == target or info.confidence == 0.0 or not llm.has_credentials:
        return None
    # The cache is built with prefix TRANSLATE_CACHE_PREFIX by the caller.
    key = hashlib.sha256(f"{target}|{_normalise(text)}".encode()).hexdigest()
    hit = await cache.get(key) if cache is not None else None
    if isinstance(hit, dict) and isinstance(hit.get("query"), str):
        return str(hit["query"])
    prompt = PROMPT.format(target=_NAMES[target], question=sanitize_for_prompt_literal(text)[:1000])
    options = dict(llm.fast_call_options())
    # A call cancelled by wait_for never reaches the provider's cost recording, so let the
    # provider time out first (just under wait_for's limit) and the spend is still counted.
    options["timeout"] = min(float(options.get("timeout") or timeout_s), timeout_s)
    try:
        response = await asyncio.wait_for(
            llm.complete_json([LLMMessage(role="user", content=prompt)], **options),
            timeout=timeout_s + 0.25,
        )
        parsed = json.loads(response.content)
        query = str(parsed.get("query", "")).strip() if isinstance(parsed, dict) else ""
    except (TimeoutError, ValueError, TypeError):
        return None
    except Exception:
        logger.warning("Query translation failed", exc_info=True)
        return None
    if not query or _normalise(query) == _normalise(text) or len(query) > 500:
        return None
    if cache is not None:
        await cache.set(key, {"query": query}, TRANSLATE_CACHE_TTL_S)
    return query
