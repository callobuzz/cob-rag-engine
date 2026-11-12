"""Retrieval presets and bounds for per-corpus configuration.

Presets are named bundles of retrieval settings. When a tenant selects a preset,
the individual values are written to corpus.retrieval_config JSONB.
Bounds define min/max for each configurable field to prevent abuse.

Engine defaults (Settings) remain unchanged — presets are corpus-level overrides.
"""

import asyncio
import functools
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

# All configurable retrieval fields.
# Each preset MUST define all of these.
#
# `top_k` and `final_k` are two different numbers and the presets used to
# conflate them: every preset set only `top_k`, describing it as the chunk
# count, so "economy" quietly narrowed SEARCH to 5 candidates and left the
# cross-encoder nothing to rerank. Width scales with the tier; the answer size
# barely moves, because context window is the expensive part.
# Note: temperature and max_tokens are LLM-level concerns — they live in llm_config only.
RETRIEVAL_FIELDS: list[str] = [
    "top_k",
    "final_k",
    "multi_query_count",
    "hyde_enabled",
    "query_classification_enabled",
    "reranking_enabled",
    "mmr_enabled",
    "mmr_lambda",
    "temporal_decay_enabled",
    "validation_enabled",
    "cache_enabled",
]

# Named presets: Economy (fast/cheap), Standard (balanced), Premium (max quality)
PRESETS: dict[str, dict[str, Any]] = {
    "economy": {
        "top_k": 10,
        "final_k": 3,
        "multi_query_count": 1,
        "hyde_enabled": False,
        "query_classification_enabled": False,
        "reranking_enabled": False,
        "mmr_enabled": False,
        "mmr_lambda": 0.7,
        "temporal_decay_enabled": False,
        "validation_enabled": False,
        "cache_enabled": True,
    },
    "standard": {
        "top_k": 20,
        "final_k": 5,
        "multi_query_count": 2,
        "hyde_enabled": False,
        "query_classification_enabled": False,
        "reranking_enabled": True,
        "mmr_enabled": True,
        "mmr_lambda": 0.7,
        "temporal_decay_enabled": True,
        "validation_enabled": False,
        "cache_enabled": True,
    },
    "premium": {
        "top_k": 40,
        "final_k": 8,
        "multi_query_count": 3,
        "hyde_enabled": True,
        "query_classification_enabled": True,
        "reranking_enabled": True,
        "mmr_enabled": True,
        "mmr_lambda": 0.5,
        "temporal_decay_enabled": True,
        "validation_enabled": True,
        "cache_enabled": True,
    },
}

# Bounds: min/max for numeric fields, allowed values for booleans.
BOUNDS: dict[str, dict[str, Any]] = {
    "top_k": {"min": 1, "max": 100, "type": "int"},
    "final_k": {"min": 1, "max": 100, "type": "int"},
    "multi_query_count": {"min": 1, "max": 5, "type": "int"},
    "mmr_lambda": {"min": 0.0, "max": 1.0, "type": "float"},
}


def validate_retrieval_config(config: dict[str, Any]) -> list[str]:
    """Validate a retrieval config dict against bounds.

    Returns a list of error messages (empty = valid).
    Unknown keys are silently ignored for forward-compatibility.
    """
    errors: list[str] = []
    for key, value in config.items():
        if key not in BOUNDS:
            continue
        bound = BOUNDS[key]
        if bound["type"] == "int":
            if not isinstance(value, int):
                errors.append(f"{key}: expected int, got {type(value).__name__}")
                continue
            if value < bound["min"] or value > bound["max"]:
                errors.append(f"{key}: {value} out of range [{bound['min']}, {bound['max']}]")
        elif bound["type"] == "float":
            if not isinstance(value, (int, float)):
                errors.append(f"{key}: expected number, got {type(value).__name__}")
                continue
            if value < bound["min"] or value > bound["max"]:
                errors.append(f"{key}: {value} out of range [{bound['min']}, {bound['max']}]")
    return errors


@functools.cache
def _gpu_available() -> bool:
    """Does this process see a CUDA GPU? Answered once; any failure to ask means no."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


async def probe_gpu() -> bool:
    """`_gpu_available` answered in a worker thread, so importing torch (seconds, and CUDA
    initialisation) never blocks the event loop. Called once at startup; the answer is
    cached, so every request afterwards reads it instantly."""
    return await asyncio.to_thread(_gpu_available)


def local_reranker_allowed(settings: "Settings | None") -> bool:
    """May a preset switch re-ranking on for a knowledge base on this engine?

    The built-in cross-encoder takes about 0.4 s a question on a GPU and about 10 s on a
    CPU, so a preset must not turn it on by itself on a machine without one. Re-ranking
    follows a preset when any of these holds:

    - a GPU is available to this process;
    - the operator turned it on for the whole engine (`RAG_RERANKING_ENABLED=true`);
    - the operator chose the API re-ranker (`RAG_RERANK_PROVIDER=llm`), which runs at
      the provider and not on this machine.
    """
    if settings is not None and (settings.reranking_enabled or settings.rerank_provider == "llm"):
        return True
    return _gpu_available()


def effective_presets(settings: "Settings | None" = None) -> dict[str, dict[str, Any]]:
    """The presets as this engine would store them: re-ranking only where it is usable."""
    allowed = local_reranker_allowed(settings)
    result: dict[str, dict[str, Any]] = {}
    for name, preset in PRESETS.items():
        values = dict(preset)
        values["reranking_enabled"] = bool(values["reranking_enabled"]) and allowed
        result[name] = values
    return result


def expand_preset(name: str, settings: "Settings | None" = None) -> dict[str, Any]:
    """Expand a preset name to its full config dict, as this engine would store it.

    Returns a copy so callers can safely modify. `settings` decides whether the preset's
    re-ranker is kept (see `local_reranker_allowed`); without it only a GPU keeps it.
    Raises ValueError if preset name is unknown.
    """
    if name not in PRESETS:
        raise ValueError(f"Unknown preset: '{name}'. Available: {list(PRESETS.keys())}")
    return effective_presets(settings)[name]
