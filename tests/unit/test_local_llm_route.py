"""PIP2: the route a local profile plugs in (finalize_llm), its cost and the LiteLLM call."""

import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver, ResolvedLLMConfig
from callobuzz_rag.llm.base import LLMMessage
from callobuzz_rag.llm.cost import priced_cost
from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
from callobuzz_rag.llm.local import local_status, reset_cache
from callobuzz_rag.tiers.apply import finalize_llm
from callobuzz_rag.tiers.lanes import lane_configs
from callobuzz_rag.tiers.resolve import resolve_with_profile
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig
from callobuzz_rag.tiers.seeds import SEEDS

S = Settings(
    llm_model="openrouter/x",
    llm_api_key="sk-real",
    local_llm_enabled=True,
    local_llm_url="http://local-llm:11434",
    local_llm_model="gemma3:4b",
)


def free(*, available: bool = True, **over: Any) -> EffectiveProfile:
    cfg = ProfileConfig.model_validate({**SEEDS["free"]["config"], **over})
    return EffectiveProfile(
        id=uuid.uuid4(),
        name="Free",
        label="Economy",
        config=cfg,
        source="plan",
        available=available,
    )


def resolved(**kw: Any) -> ResolvedLLMConfig:
    base: dict[str, Any] = {
        "model": "openrouter/x",
        "api_key": "sk-real",
        "base_url": None,
        "temperature": 0.1,
        "max_tokens": 1024,
        "payer": "operator",
        "fast_model": "openrouter/fast",
        "fast_provider_order": ["DeepInfra"],
        "language_models": {"hi": "openrouter/hindi"},
    }
    base.update(kw)
    return ResolvedLLMConfig(**base)


@pytest.fixture(autouse=True)
def _clean() -> None:
    reset_cache()


def transport(*models: str) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda r: httpx.Response(200, json={"models": [{"name": m} for m in models]})
    )


def test_finalize_local_route() -> None:
    llm = finalize_llm(resolved(), free(), S)
    assert (llm.provider, llm.model, llm.base_url) == (
        "ollama_chat",
        "gemma3:4b",
        "http://local-llm:11434",
    )
    assert llm.api_key == "local" and llm.payer == "operator" and llm.thinking == "off"
    assert llm.fast_provider_order == [] and llm.fast_model is None
    assert "sk-real" not in repr(llm)


def test_a_profile_picked_local_model_wins() -> None:
    assert finalize_llm(resolved(), free(answer_model="qwen3:4b"), S).model == "qwen3:4b"


def test_a_business_paying_its_own_ai_keeps_its_route() -> None:
    byok = resolved(payer="byok", api_key="sk-theirs")
    assert finalize_llm(byok, free(), S) is byok


def test_not_ready_never_reaches_the_paid_key() -> None:
    out = finalize_llm(resolved(), free(available=False), S)
    assert out.api_key == "" and out.provider != "ollama_chat"
    assert "sk-real" not in repr(out)


def test_a_business_paying_its_own_ai_is_untouched_even_when_not_ready() -> None:
    byok = resolved(payer="byok", api_key="sk-theirs")
    assert finalize_llm(byok, free(available=False), S) is byok


def test_local_off_for_the_install_changes_nothing() -> None:
    cfg = resolved()
    assert finalize_llm(cfg, free(), Settings(local_llm_enabled=False)) is cfg


def test_the_engine_route_is_untouched() -> None:
    cfg = resolved()
    standard = EffectiveProfile(
        id=None,
        name="Standard",
        label="Standard",
        config=ProfileConfig(),
        source="default",
        available=True,
    )
    assert finalize_llm(cfg, standard, S) is cfg


async def test_local_language_map_keeps_only_listed_models() -> None:
    await local_status(S, transport=transport("gemma3:4b", "qwen3:4b"), warm=False)
    profile = free(language_models={"hi": "qwen3:4b", "ta": "not-pulled:1b"})
    assert finalize_llm(resolved(), profile, S).language_models == {"hi": "qwen3:4b"}


async def test_an_unlisted_fast_model_is_ignored() -> None:
    await local_status(S, transport=transport("gemma3:4b"), warm=False)
    assert finalize_llm(resolved(), free(fast_model="nope:1b"), S).fast_model is None
    await local_status(S, transport=transport("gemma3:4b", "qwen3:1b"), warm=False)
    assert finalize_llm(resolved(), free(fast_model="qwen3:1b"), S).fast_model == "qwen3:1b"


def test_resolve_with_profile_goes_local_for_the_free_profile() -> None:
    corpus = SimpleNamespace(llm_config={}, retrieval_config={})
    llm, _ = resolve_with_profile(
        ConfigResolver(S), corpus, headers=None, profile=free(), settings=S
    )
    assert llm.provider == "ollama_chat" and llm.payer == "operator"


def test_local_calls_are_priced_zero_and_known() -> None:
    assert priced_cost("gemma3:4b", "ollama_chat", 1200, 300) == 0.0


def test_the_pool_reads_the_local_slot_setting() -> None:
    cfgs = lane_configs(Settings(local_llm_slots=3, local_llm_timeout_seconds=400))
    assert cfgs["local"].slots == 3 and cfgs["local"].lease_s == 400


def test_the_litellm_call_carries_the_local_route() -> None:
    llm = finalize_llm(resolved(), free(), S)
    provider = LiteLLMProvider.from_resolved_config(llm)
    kwargs = provider._base_kwargs(
        [LLMMessage(role="user", content="hi")], model=None, temperature=None, max_tokens=None
    )
    assert kwargs["custom_llm_provider"] == "ollama_chat"
    assert kwargs["api_base"] == "http://local-llm:11434"
    assert kwargs["api_key"] == "local" and kwargs["model"] == "gemma3:4b"
    assert "sk-real" not in repr(kwargs)


async def test_a_profile_model_the_server_does_not_hold_is_not_available() -> None:
    from callobuzz_rag.tiers.profiles import effective


    await local_status(S, transport=transport("gemma3:4b"), warm=False)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        name="Free",
        label="Economy",
        config={**SEEDS["free"]["config"], "answer_model": "qwen3:4b"},
    )
    assert effective(row, "plan", settings=S).available is False  # type: ignore[arg-type]
    row.config["answer_model"] = "gemma3:4b"
    assert effective(row, "plan", settings=S).available is True  # type: ignore[arg-type]
    row.config.pop("answer_model")
    assert effective(row, "plan", settings=S).available is True  # type: ignore[arg-type]
