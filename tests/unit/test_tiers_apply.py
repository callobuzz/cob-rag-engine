"""PIP1: the overlay, row by row of the README table, and `resolve_with_profile`."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver
from callobuzz_rag.tiers.apply import local_ready, runtime_with_profile
from callobuzz_rag.tiers.resolve import resolve_with_profile
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig
from callobuzz_rag.tiers.seeds import SEEDS

ENGINE = Settings(
    llm_model="engine/model",
    llm_fast_model="engine/fast",
    llm_max_tokens=2048,
    llm_api_key="sk-engine",
    query_top_k=40,
    query_final_k=10,
    multi_query_count=3,
    hyde_enabled=True,
    reranking_enabled=False,
)
KB = {"model": "kb/model", "thinking": "high", "fast_model": "kb/fast"}


def cfg(**kwargs: Any) -> ProfileConfig:
    return ProfileConfig.model_validate(kwargs)


def eff(config: ProfileConfig, *, available: bool = True) -> EffectiveProfile:
    return EffectiveProfile(
        id=uuid.uuid4(), name="P", label="P", config=config, source="plan", available=available
    )


def corpus(llm: dict | None = None, retrieval: dict | None = None, *, byok: bool = False) -> Any:
    llm_config = dict(llm or {})
    if byok:
        llm_config["api_key_encrypted"] = "enc"
    return SimpleNamespace(llm_config=llm_config, retrieval_config=dict(retrieval or {}))


class _Encryptor:
    def decrypt(self, value: str) -> str:
        return "sk-their-own"


def resolver() -> ConfigResolver:
    return ConfigResolver(ENGINE, encryptor=_Encryptor())  # type: ignore[arg-type]


def resolve(profile: EffectiveProfile, kb: Any, headers: dict | None = None) -> tuple[Any, Any]:
    return resolve_with_profile(resolver(), kb, headers=headers, profile=profile, settings=ENGINE)


@pytest.mark.parametrize(
    ("profile", "kb", "byok", "want_model", "want_fast"),
    [
        (cfg(), KB, False, "kb/model", "kb/fast"),  # Standard: no-op
        (cfg(), {}, False, "engine/model", "engine/fast"),
        (cfg(answer_model="p/model"), {}, False, "p/model", "engine/fast"),  # fills unset
        (cfg(answer_model="p/model"), KB, False, "kb/model", "kb/fast"),  # knowledge base first
        (cfg(answer_model="p/model", overrides_kb_settings=True), KB, False, "p/model", "kb/fast"),
        (
            cfg(answer_model="p/model", fast_model="p/fast", overrides_kb_settings=True),
            KB,
            False,
            "p/model",
            "p/fast",
        ),
        (cfg(fast_model="p/fast"), {}, False, "engine/model", "p/fast"),
        (
            cfg(answer_model="p/model", fast_model="p/fast", overrides_kb_settings=True),
            KB,
            True,
            "kb/model",
            "kb/fast",
        ),  # BYOK ignores the model fields
    ],
)
def test_model_precedence(
    profile: ProfileConfig, kb: dict, byok: bool, want_model: str, want_fast: str
) -> None:
    llm, _ = resolve(eff(profile), corpus(kb, byok=byok))
    assert (llm.model, llm.fast_model) == (want_model, want_fast)
    assert llm.payer == ("byok" if byok else "operator")


def test_payer_never_flips() -> None:
    llm, _ = resolve(
        eff(cfg(answer_model="p/m", overrides_kb_settings=True)), corpus({}, byok=True)
    )
    assert llm.payer == "byok" and llm.api_key == "sk-their-own"


def test_thinking_follows_the_same_precedence() -> None:
    llm, _ = resolve(eff(cfg(thinking="low", fast_thinking="minimal")), corpus(KB))
    assert (llm.thinking, llm.fast_thinking) == ("high", "minimal")  # kb thinking wins, fast fills
    llm, _ = resolve(eff(cfg(thinking="low", overrides_kb_settings=True)), corpus(KB))
    assert llm.thinking == "low"


def test_max_tokens_is_only_a_cap() -> None:
    llm, _ = resolve(eff(cfg(max_answer_tokens=600)), corpus())
    assert llm.max_tokens == 600
    llm, _ = resolve(eff(cfg(max_answer_tokens=4096)), corpus())
    assert llm.max_tokens == 2048
    llm, _ = resolve(eff(cfg(max_answer_tokens=600)), corpus(byok=True))
    assert llm.max_tokens == 2048  # BYOK ignores it


def test_a_model_the_caller_picked_is_never_replaced() -> None:
    llm, _ = resolve(
        eff(cfg(answer_model="p/model", overrides_kb_settings=True)),
        corpus(),
        headers={"X-LLM-Model": "caller/model"},
    )
    assert llm.model == "caller/model"


def test_language_map_none_follows_empty_disables_and_a_map_replaces() -> None:
    engine_map = Settings(**{**ENGINE.model_dump(), "llm_language_models": {"hi": "engine/hindi"}})

    def run(profile: ProfileConfig, kb: dict) -> dict[str, str]:
        llm, _ = resolve_with_profile(
            ConfigResolver(engine_map),
            corpus(kb),
            headers=None,
            profile=eff(profile),
            settings=engine_map,
        )
        return llm.language_models

    assert run(cfg(), {}) == {"hi": "engine/hindi"}  # None follows
    assert run(cfg(language_models={}), {}) == {}  # {} switches routing off
    assert run(cfg(language_models={"hi": "p/hindi"}), {}) == {"hi": "p/hindi"}
    # the knowledge base's own map beats a non-overriding profile; an overriding one replaces it
    kb_map = {"language_models": {"hi": "kb/hindi"}}
    assert run(cfg(language_models={"hi": "p/hindi"}), kb_map) == {"hi": "kb/hindi"}
    assert run(cfg(language_models={"hi": "p/hindi"}, overrides_kb_settings=True), kb_map) == {
        "hi": "p/hindi"
    }
    # a profile's own answer model beats the engine's language map
    assert run(cfg(answer_model="p/model"), {}) == {}


@pytest.mark.parametrize(
    ("choice", "kb", "enabled", "provider"),
    [
        ("inherit", {"reranking_enabled": True, "rerank_provider": "llm"}, True, "llm"),
        ("inherit", {}, False, "local"),
        ("off", {"reranking_enabled": True}, True, "local"),  # fill-unset: the knowledge base wins
        ("off", {}, False, "local"),
        ("local", {}, True, "local"),
        ("llm", {}, True, "llm"),
        ("api", {}, True, "api"),
        ("api", {"rerank_provider": "llm"}, True, "llm"),  # the knowledge base chose its own
    ],
)
def test_reranker_choice(choice: str, kb: dict, enabled: bool, provider: str) -> None:
    extra = {"rerank_model": "cohere/rerank-v3.5"} if choice == "api" else {}
    _, retrieval = resolve(eff(cfg(reranker=choice, **extra)), corpus(retrieval=kb))
    assert (retrieval.reranking_enabled, retrieval.rerank_provider) == (enabled, provider)


def test_off_with_override_turns_reranking_off() -> None:
    _, r = resolve(
        eff(cfg(reranker="off", overrides_kb_settings=True)),
        corpus(retrieval={"reranking_enabled": True}),
    )
    assert r.reranking_enabled is False


def test_api_reranker_carries_its_model_and_llm_reranker_may_pin_one() -> None:
    _, r = resolve(eff(cfg(reranker="api", rerank_model="cohere/rerank-v3.5")), corpus())
    assert r.rerank_llm_model == "cohere/rerank-v3.5"
    _, r = resolve(eff(cfg(reranker="llm", rerank_model="p/ranker")), corpus())
    assert r.rerank_llm_model == "p/ranker"
    _, r = resolve(eff(cfg(reranker="llm")), corpus())
    assert r.rerank_llm_model == ENGINE.rerank_llm_model  # follows the engine


def test_retrieval_knobs() -> None:
    _, r = resolve(eff(cfg(multi_query_count=0, hyde_enabled=False)), corpus())
    assert (r.multi_query_count, r.hyde_enabled) == (0, False)  # rewrites off
    _, r = resolve(eff(cfg(multi_query_count=0)), corpus(retrieval={"multi_query_count": 2}))
    assert r.multi_query_count == 2  # the knowledge base's own choice stands
    _, r = resolve(
        eff(cfg(multi_query_count=0, overrides_kb_settings=True)),
        corpus(retrieval={"multi_query_count": 2}),
    )
    assert r.multi_query_count == 0
    _, r = resolve(eff(cfg(top_k=5)), corpus())
    assert (r.top_k, r.final_k) == (5, 5)  # never more passages than the search pulled in
    _, r = resolve(eff(cfg(top_k=30, final_k=6)), corpus())
    assert (r.top_k, r.final_k) == (30, 6)


def test_retrieval_knobs_apply_to_a_business_with_its_own_key_too() -> None:
    _, r = resolve(eff(cfg(multi_query_count=0)), corpus(byok=True))
    assert r.multi_query_count == 0


def test_standard_profile_changes_nothing() -> None:
    kb = corpus(KB, {"top_k": 12})
    plain_llm = resolver().resolve_llm(None, kb.llm_config)
    plain_retrieval = resolver().resolve_retrieval(kb.retrieval_config)
    llm, retrieval = resolve(eff(ProfileConfig()), kb)
    assert llm == plain_llm and retrieval == plain_retrieval


def test_local_route_without_pip2_is_unavailable() -> None:
    from callobuzz_rag.tiers.profiles import effective

    row = SimpleNamespace(
        id=uuid.uuid4(), name="Free", label="Economy", config=SEEDS["free"]["config"]
    )
    assert not effective(row, "plan").available  # type: ignore[arg-type]
    assert local_ready(ENGINE) is False


def test_an_unavailable_local_profile_still_applies_its_cheap_settings() -> None:
    free = eff(ProfileConfig.model_validate(SEEDS["free"]["config"]), available=False)
    llm, r = resolve(free, corpus(KB, {"reranking_enabled": True, "multi_query_count": 3}))
    assert (r.reranking_enabled, r.multi_query_count, r.hyde_enabled) == (False, 0, False)
    assert llm.max_tokens == 600 and llm.payer == "operator"


def test_followups_off_is_applied_to_the_runtime_and_never_switched_on() -> None:
    from callobuzz_rag.conversation.schema import AssistantConfig
    from callobuzz_rag.conversation.store import RuntimeConfig

    base = AssistantConfig()

    runtime = RuntimeConfig(config=base, version=1, draft_revision=None, enabled=True)
    assert runtime.config.conversation.ai_followups is True
    off = runtime_with_profile(runtime, eff(cfg(followups=False)))
    assert off.config.conversation.ai_followups is False
    assert runtime.config.conversation.ai_followups is True  # the original is untouched
    assert runtime_with_profile(runtime, eff(cfg())) is runtime
    switched_off = runtime_with_profile(
        RuntimeConfig(
            config=base.model_copy(
                update={
                    "conversation": base.conversation.model_copy(update={"ai_followups": False})
                }
            ),
            version=1,
            draft_revision=None,
            enabled=True,
        ),
        eff(cfg(followups=True)),
    )
    assert switched_off.config.conversation.ai_followups is False  # a profile never turns it on


def test_a_business_paying_its_own_ai_never_gets_the_llm_reranker_from_a_profile() -> None:
    premium = eff(ProfileConfig.model_validate(SEEDS["premium"]["config"]))
    llm, r = resolve(premium, corpus(KB, {"reranking_enabled": False}, byok=True))
    assert llm.payer != "operator"
    assert r.reranking_enabled is False  # its own choice stands
    assert r.rerank_provider == ENGINE.rerank_provider  # not "llm"
    assert r.rerank_llm_model == ENGINE.rerank_llm_model
    # the deployer's own key still gets it
    llm, r = resolve(premium, corpus(KB))
    assert llm.payer == "operator" and (r.reranking_enabled, r.rerank_provider) == (True, "llm")
    # off and local do not spend the business's key, so they still apply
    llm, r = resolve(
        eff(cfg(reranker="local", overrides_kb_settings=True)),
        corpus(KB, {"reranking_enabled": False}, byok=True),
    )
    assert (r.reranking_enabled, r.rerank_provider) == (True, "local")
