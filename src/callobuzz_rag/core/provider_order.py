"""OpenRouter host pinning for the fast model (RAG_LLM_FAST_PROVIDER_ORDER,
llm_config.fast_provider_order).

OpenRouter serves most open models from several hosts (Groq, Cerebras, DeepInfra, ...) whose
latency differs by seconds. A request may name the hosts to try first in its body:
`{"provider": {"order": [...], "allow_fallbacks": true}}` (openrouter.ai/docs/features/
provider-routing). Fallbacks stay on, so a host that is down costs speed, never the answer.
"""

import re
from collections.abc import Iterable
from typing import Any

MAX_PROVIDERS = 8
# OpenRouter slugs: "groq", "cerebras", "deepinfra/turbo", "google-vertex/us-east5".
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*$")


def clean_provider_order(values: Iterable[str]) -> list[str]:
    """Lower-cased, trimmed slugs, empties dropped. Raises ValueError on a bad slug or too many."""
    out = [v.strip().lower() for v in values if v and v.strip()]
    bad = [v for v in out if len(v) > 64 or not _SLUG.match(v)]
    if bad:
        raise ValueError(f"not an OpenRouter provider slug: {', '.join(bad)}")
    if len(out) > MAX_PROVIDERS:
        raise ValueError(f"at most {MAX_PROVIDERS} providers")
    return out


def parse_provider_order(value: object) -> list[str]:
    """A comma-separated string (the env var) or a list."""
    if value is None:
        return []
    if isinstance(value, str):
        return clean_provider_order(value.split(","))
    if isinstance(value, list | tuple):
        return clean_provider_order(str(v) for v in value)
    raise ValueError("expected a comma-separated string or a list of provider slugs")


def is_openrouter(provider: str | None, model: str) -> bool:
    return provider == "openrouter" or (not provider and model.startswith("openrouter/"))


def provider_body(order: list[str]) -> dict[str, Any]:
    """The request-body field OpenRouter reads."""
    return {"provider": {"order": list(order), "allow_fallbacks": True}}
