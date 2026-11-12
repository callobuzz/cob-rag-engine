"""Lay a pipeline profile over a knowledge base's settings (PIP1).

Pure functions: no database, no network. `resolve.py` is the one caller that matters.

Who wins, per field group (README "Overlay rules"):

- Model fields (`answer_model`, `thinking`, `language_models`, `fast_model`, `fast_thinking`):
  only when the business is paid for by the engine's own key (the operator pays). A business
  that brings its own key (BYOK) keeps its own configuration. A profile with
  `overrides_kb_settings` replaces the knowledge base's value (profile, then engine); any other
  profile only fills what the knowledge base left unset (knowledge base, then profile, then
  engine). A model the API caller picked with `X-LLM-Model` is never replaced.
- Retrieval fields (`reranker`, `top_k`, `final_k`, `multi_query_count`, `hyde_enabled`,
  `query_classification_enabled`): the same precedence, for every payer (the CPU is the
  deployer's either way).
- `max_answer_tokens` only ever lowers the answer length. `followups=False` only ever turns
  the AI follow-up chips off. `priority` and `max_concurrency` always apply (they are read by
  the lanes, not here).

`payer` is decided before any overlay (by the resolver, from whose key it found), and nothing
here changes it, so a profile can never turn a business's own key into the deployer's or the
reverse.
"""

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from callobuzz_rag.llm.language_routing import clean_language_models
from callobuzz_rag.llm.thinking import clean_thinking
from callobuzz_rag.tiers.schema import EffectiveProfile

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from callobuzz_rag.conversation.store import RuntimeConfig
    from callobuzz_rag.core.config_resolver import ResolvedLLMConfig, ResolvedRetrievalConfig


def local_ready(settings: Any) -> bool:
    """Whether the free local model can answer right now (what `llm/local.py` last saw; no I/O)."""
    # PIP2 local
    from callobuzz_rag.llm.local import local_ready as _ready

    return _ready(settings)
    # /PIP2 local


def _kb_set(kb: dict[str, Any] | None, *keys: str) -> bool:
    """Whether the knowledge base's own config sets any of `keys`."""
    stored = kb or {}
    return any(stored.get(key) is not None for key in keys)


def _take(profile: EffectiveProfile, kb: dict[str, Any] | None, *keys: str) -> bool:
    """Whether a profile value beats the knowledge base's for the field stored under `keys`."""
    return profile.config.overrides_kb_settings or not _kb_set(kb, *keys)


def overlay_llm_config(
    llm: "ResolvedLLMConfig",
    profile: EffectiveProfile,
    *,
    kb_llm: dict[str, Any] | None,
    header_model: bool = False,
) -> "ResolvedLLMConfig":
    """The profile's model choices over a resolved LLM config (see the module docstring)."""
    cfg = profile.config
    if getattr(llm, "payer", "operator") != "operator":
        return llm  # BYOK: they pay, so their configuration stands (D4)

    changes: dict[str, Any] = {}
    if cfg.answer_model is not None and not header_model and _take(profile, kb_llm, "model"):
        changes["model"] = cfg.answer_model
        if cfg.language_models is None:
            # A model the profile picked beats the engine's language map, as a knowledge base's
            # own model does; the knowledge base's own entries still route.
            changes["language_models"] = _kb_language_models(kb_llm)
    if (
        cfg.language_models is not None
        and not header_model
        and _take(profile, kb_llm, "language_models")
    ):
        changes["language_models"] = dict(cfg.language_models)
    if cfg.thinking is not None and _take(profile, kb_llm, "thinking", "reasoning_effort"):
        changes["thinking"] = cfg.thinking
    if cfg.fast_model is not None and (
        profile.config.overrides_kb_settings or not (kb_llm or {}).get("fast_model")
    ):
        changes["fast_model"] = cfg.fast_model
    if cfg.fast_thinking is not None and _take(profile, kb_llm, "fast_thinking"):
        changes["fast_thinking"] = clean_thinking(cfg.fast_thinking) or "off"
    if cfg.max_answer_tokens is not None:
        changes["max_tokens"] = min(llm.max_tokens, cfg.max_answer_tokens)
    return replace(llm, **changes) if changes else llm


def _kb_language_models(kb_llm: dict[str, Any] | None) -> dict[str, str]:
    stored = (kb_llm or {}).get("language_models")
    if stored is None:
        return {}
    try:
        return clean_language_models(stored)
    except ValueError:
        return {}


def overlay_retrieval_config(
    retrieval: "ResolvedRetrievalConfig",
    profile: EffectiveProfile,
    *,
    kb_retrieval: dict[str, Any] | None,
    payer: str = "operator",
) -> "ResolvedRetrievalConfig":
    """The profile's retrieval choices over a resolved retrieval config.

    The `llm` reranker runs on the request's LLM credential, so for a business paying its own
    AI (`payer != "operator"`, D4) a profile never switches it on; off, local and api (the
    deployer's own rerank key) apply for every payer."""
    cfg = profile.config
    reranker = cfg.reranker
    if reranker == "llm" and payer != "operator":
        reranker = "inherit"
    changes: dict[str, Any] = {}

    for field_name in (
        "top_k",
        "final_k",
        "multi_query_count",
        "hyde_enabled",
        "query_classification_enabled",
    ):
        value = getattr(cfg, field_name)
        if value is not None and _take(profile, kb_retrieval, field_name):
            changes[field_name] = value

    if reranker != "inherit":
        if _take(profile, kb_retrieval, "reranking_enabled"):
            changes["reranking_enabled"] = reranker != "off"
        if reranker in ("local", "llm", "api") and _take(profile, kb_retrieval, "rerank_provider"):
            changes["rerank_provider"] = reranker
        # The ranking model of the `llm` reranker, or the rerank id of the `api` one (which
        # is meaningless to any other reranker, so it always travels with the choice).
        if cfg.rerank_model and (
            changes.get("rerank_provider") == "api"
            or (reranker == "llm" and _take(profile, kb_retrieval, "rerank_llm_model"))
        ):
            changes["rerank_llm_model"] = cfg.rerank_model
    # The final answer can never use more passages than the search pulled in.
    if "top_k" in changes or "final_k" in changes:
        top_k = changes.get("top_k", retrieval.top_k)
        if changes.get("final_k", retrieval.final_k) > top_k:
            changes["final_k"] = top_k
    return replace(retrieval, **changes) if changes else retrieval


def finalize_llm(
    llm: "ResolvedLLMConfig", profile: EffectiveProfile, settings: Any
) -> "ResolvedLLMConfig":
    """The last step: a profile that answers with the local model points the whole config at
    it (one route for the answer, the fast model and the language map: D5). A business paying
    its own AI keeps its own route, and a local model that is not ready changes nothing (the
    gate then pauses, D14)."""
    # PIP2 local
    if profile.config.answer_route != "local" or getattr(llm, "payer", "operator") != "operator":
        return llm
    if not profile.available:
        # Belt and braces: a surface that skipped the gate must still never reach the
        # deployer's paid key with a free business's question.
        logger.warning("Local profile answered while the local model is not ready; key removed")
        return replace(llm, api_key="")
    if not _local_on(settings):
        return llm
    from callobuzz_rag.llm.local import local_models, local_route, same_model

    route = local_route(settings, profile.config.answer_model)
    listed = local_models(settings)

    def on_server(model: str) -> bool:  # `qwen3` is the server's `qwen3:latest`
        return any(same_model(held, model) for held in listed)

    chosen = profile.config.language_models
    languages: dict[str, str] = {}
    for code, model in (chosen if chosen is not None else {}).items():
        if on_server(model):
            languages[code] = model
        else:
            logger.warning("Local profile language model %r is not on the server; ignored", model)
    fast = profile.config.fast_model
    if fast is not None and not on_server(fast):
        logger.warning("Local profile fast model %r is not on the server; ignored", fast)
        fast = None
    return replace(
        llm,
        provider=route.provider,
        model=route.model,
        base_url=route.base_url,
        api_key=route.api_key,
        payer="operator",
        thinking="off",
        fast_model=fast,
        fast_thinking="off",
        fast_provider_order=[],
        language_models=languages,
        timeout=route.timeout,
        fast_timeout=route.timeout,
    )
    # /PIP2 local


def _local_on(settings: Any) -> bool:
    return bool(getattr(settings, "local_llm_enabled", False))


def runtime_with_profile(runtime: "RuntimeConfig", profile: EffectiveProfile) -> "RuntimeConfig":
    """The assistant's runtime config for one turn under `profile`: AI follow-up chips are
    switched off when the profile says so (never switched on)."""
    if profile.config.followups or not runtime.config.conversation.ai_followups:
        return runtime
    conversation = runtime.config.conversation.model_copy(update={"ai_followups": False})
    return replace(runtime, config=runtime.config.model_copy(update={"conversation": conversation}))
