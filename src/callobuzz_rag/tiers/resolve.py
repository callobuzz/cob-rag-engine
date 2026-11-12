"""The one way an answering surface resolves its configuration (PIP1).

`resolve_with_profile` replaces `ConfigResolver.resolve_llm` + `resolve_retrieval` at every
surface that answers for a business and at every AI helper that works on its knowledge base.
`tests/unit/test_profile_everywhere.py` checks that no surface still calls the resolver's
`resolve_llm` directly. Not used by COB (it answers with its own configuration), by the RAGAS
judge or by the optimizer's trials.
"""

from typing import Any

from callobuzz_rag.billing.byok import usable_headers, usable_llm_config  # P5 plans
from callobuzz_rag.core.config_resolver import (
    ConfigResolver,
    ResolvedLLMConfig,
    ResolvedRetrievalConfig,
)
from callobuzz_rag.tiers.apply import finalize_llm, overlay_llm_config, overlay_retrieval_config
from callobuzz_rag.tiers.schema import EffectiveProfile

# The knowledge base's own model choices: what a downgrade does not inherit.
_MODEL_CHOICES = frozenset(
    {
        "model",
        "language_models",
        "thinking",
        "reasoning_effort",
        "fast_model",
        "fast_thinking",
        "fast_provider_order",
    }
)


def resolve_with_profile(
    resolver: ConfigResolver,
    corpus: Any,
    *,
    headers: dict[str, str] | None,
    profile: EffectiveProfile,
    settings: Any,
    llm_config_override: dict[str, Any] | None = None,
    retrieval_config_override: dict[str, Any] | None = None,
) -> tuple[ResolvedLLMConfig, ResolvedRetrievalConfig]:
    """The knowledge base's LLM and retrieval configuration with `profile` laid over it.

    `llm_config_override` / `retrieval_config_override` replace the knowledge base's stored
    config for this call (guided setup answering with the owner's draft key, a golden run's
    retrieval overrides); the profile is laid over whatever they hold.
    """
    kb_llm = (
        llm_config_override
        if llm_config_override is not None
        else getattr(corpus, "llm_config", None)
    )
    kb_retrieval = (
        retrieval_config_override
        if retrieval_config_override is not None
        else getattr(corpus, "retrieval_config", None)
    )
    # P5 plans: a business whose plan has no own-key allowance answers on the deployer's model
    # and key. The stored key is kept, never used; a key or address sent per request is ignored.
    # Done first, so the payer below is already `operator` and every gate, meter and charge
    # downstream follows unchanged.
    kb_llm = usable_llm_config(kb_llm, allowed=profile.byok_allowed)
    headers = usable_headers(headers, allowed=profile.byok_allowed) or None
    # /P5 plans
    # Resolved without the profile first: the payer is whoever's key the plain resolution found.
    llm = resolver.resolve_llm(request_headers=headers or None, corpus_llm_config=kb_llm)
    if profile.source == "downgrade" and llm.payer == "operator":
        # A downgraded answer is charged nothing, so the deployer's key pays for all of it: it
        # runs on the target's economy, never on a model the caller picked with `X-LLM-Model`
        # or the knowledge base chose for itself (the target, then the engine's defaults).
        headers = {k: v for k, v in (headers or {}).items() if k.lower() != "x-llm-model"}
        kb_llm = {k: v for k, v in (kb_llm or {}).items() if k not in _MODEL_CHOICES}
        llm = resolver.resolve_llm(request_headers=headers or None, corpus_llm_config=kb_llm)
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    retrieval = resolver.resolve_retrieval(corpus_retrieval_config=kb_retrieval)
    llm = overlay_llm_config(
        llm, profile, kb_llm=kb_llm, header_model=lowered.get("x-llm-model") is not None
    )
    llm = finalize_llm(llm, profile, settings)
    retrieval = overlay_retrieval_config(
        retrieval, profile, kb_retrieval=kb_retrieval, payer=llm.payer
    )
    return llm, retrieval
