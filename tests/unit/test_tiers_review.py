"""PIP review fixes for the pipeline tiers: a downgrade really is the target's economy, a BYOK
turn on a local profile uses the engine pool, the local pool has its own limits."""

from callobuzz_rag.tiers.profiles import downgraded
from tests.unit.test_tiers_apply import KB, cfg, corpus, eff, resolve

CHEAP = "cheap/model"


def test_a_downgrade_replaces_the_knowledge_bases_expensive_model() -> None:
    """PIP-3: the target does not say 'use these settings instead'; a downgrade is waived of
    charges, so the knowledge base's own model must never keep running at the operator's cost."""
    target = eff(cfg(answer_model=CHEAP))  # overrides_kb_settings left off
    llm, _ = resolve(downgraded(target), corpus(KB))
    assert llm.model == CHEAP and llm.payer == "operator"


def test_a_downgrade_without_a_model_falls_back_to_the_engines_not_the_knowledge_bases() -> None:
    llm, _ = resolve(downgraded(eff(cfg())), corpus(KB))
    assert llm.model == "engine/model" and llm.fast_model == "engine/fast"


def test_a_caller_cannot_pick_a_model_on_the_operators_key_while_downgraded() -> None:
    headers = {"X-LLM-Model": "openai/gpt-5"}
    llm, _ = resolve(downgraded(eff(cfg(answer_model=CHEAP))), corpus(KB), headers)
    assert llm.model == CHEAP and llm.payer == "operator"
    llm, _ = resolve(downgraded(eff(cfg())), corpus({}), headers)
    assert llm.model == "engine/model"


def test_a_caller_with_their_own_key_keeps_their_model_while_downgraded() -> None:
    headers = {"X-LLM-Model": "openai/gpt-5", "X-LLM-API-Key": "sk-theirs"}
    llm, _ = resolve(downgraded(eff(cfg(answer_model=CHEAP))), corpus({}), headers)
    assert llm.payer == "byok" and llm.model == "openai/gpt-5"


def test_the_header_model_still_wins_on_a_normal_profile() -> None:
    llm, _ = resolve(eff(cfg(answer_model=CHEAP)), corpus({}), {"X-LLM-Model": "x/y"})
    assert llm.model == "x/y"


# ----------------------------------------------------- PIP-4: a BYOK turn never takes a CPU slot

import uuid  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from callobuzz_rag.billing.enforcement import NOT_BILLED  # noqa: E402
from callobuzz_rag.core.config import Settings  # noqa: E402
from callobuzz_rag.fairuse import gate  # noqa: E402
from callobuzz_rag.fairuse.limits import (  # noqa: E402
    EffectiveFairUse,
    FairUseLimits,
    FairUsePolicy,
)
from callobuzz_rag.tiers.slot import effective_pool, slot_spec, worker_slot  # noqa: E402


def local_profile(**kw: object):
    return eff(cfg(answer_route="local", overrides_kb_settings=True, priority="low", **kw))


def test_a_business_with_its_own_key_does_not_queue_for_the_cpu_model() -> None:
    from dataclasses import replace

    free = local_profile()
    assert effective_pool(free) == "local"
    assert effective_pool(replace(free, own_key=True)) == "engine"
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(_env_file=None, answer_slots=4, local_llm_slots=1),
            answer_lanes=MagicMock(),
        )
    )
    assert slot_spec(app, replace(free, own_key=True)).pool == "engine"  # type: ignore[union-attr]
    assert slot_spec(app, free).pool == "local"  # type: ignore[union-attr]


async def test_the_gate_marks_a_byok_answer_on_a_local_profile() -> None:
    free = local_profile()
    req = SimpleNamespace(
        state=SimpleNamespace(is_admin=False),
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(fair_use_enabled=True))),
    )
    eff_fair = EffectiveFairUse(
        limits=FairUseLimits(),
        policy=FairUsePolicy(),
        exempt=False,
        overridden=frozenset(),
        tenant_name="T",
    )
    with (
        patch.object(gate, "effective_fair_use", AsyncMock(return_value=eff_fair)),
        patch.object(gate, "enforce_question_cap", AsyncMock()),
        patch.object(gate, "enforce_billing", AsyncMock(return_value=NOT_BILLED)),
    ):
        corpus_row = SimpleNamespace(tenant_id=uuid.uuid4())
        byok = await gate.answer_gate(
            AsyncMock(), req, corpus_row, payer="byok", surface="widget", plan_profile=free
        )
        operator = await gate.answer_gate(
            AsyncMock(), req, corpus_row, payer="operator", surface="widget", plan_profile=free
        )
    assert byok.profile.own_key is True and operator.profile.own_key is False


def test_worker_slot_uses_the_same_pool_rule() -> None:
    from dataclasses import replace

    settings = Settings(_env_file=None, local_llm_slots=1)
    spec, _closer = worker_slot(settings, replace(local_profile(), own_key=True))
    assert spec is None or spec.pool == "engine"


# --------------------------------------- PIP-5: the local pool is not halved for low priority


def test_the_local_pool_lets_every_priority_use_every_slot() -> None:
    """The Free profile and every downgrade are priority `low`: with RAG_LOCAL_LLM_SLOTS=2 both
    slots must be usable by them (they are the only traffic the local pool has)."""
    from callobuzz_rag.tiers.lanes import allowed_slots, lane_configs

    local = lane_configs(Settings(_env_file=None, local_llm_slots=2, answer_slots_reserved_high=1))[
        "local"
    ]
    for priority in ("low", "normal", "high"):
        assert list(allowed_slots(priority, local)) == [0, 1], priority  # type: ignore[arg-type]
    engine = lane_configs(Settings(_env_file=None, answer_slots=4, answer_slots_reserved_high=1))[
        "engine"
    ]
    assert list(allowed_slots("low", engine)) == [0, 1]  # the engine pool keeps its rules


# --------------------------- PIP-11: a bare model tag matches the server's `:latest` listing


async def test_a_bare_language_or_fast_model_matches_the_servers_latest_tag() -> None:
    import httpx

    from callobuzz_rag.core.config_resolver import ConfigResolver
    from callobuzz_rag.llm import local as local_mod
    from callobuzz_rag.tiers.resolve import resolve_with_profile

    settings = Settings(_env_file=None, local_llm_enabled=True, local_llm_model="gemma3:4b")
    local_mod.reset_cache()
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200, json={"models": [{"name": "gemma3:4b"}, {"name": "qwen3:latest"}]}
        )
    )
    await local_mod.local_status(settings, transport=transport, warm=False)
    profile = eff(
        cfg(
            answer_route="local",
            overrides_kb_settings=True,
            language_models={"hi": "qwen3"},
            fast_model="qwen3",
        )
    )
    llm, _ = resolve_with_profile(
        ConfigResolver(settings), corpus({}), headers=None, profile=profile, settings=settings
    )
    assert llm.language_models == {"hi": "qwen3"}  # kept as configured, not dropped
    assert llm.fast_model == "qwen3"
    gone = eff(
        cfg(
            answer_route="local",
            overrides_kb_settings=True,
            language_models={"hi": "llama3"},
            fast_model="llama3",
        )
    )
    llm, _ = resolve_with_profile(
        ConfigResolver(settings), corpus({}), headers=None, profile=gone, settings=settings
    )
    assert llm.language_models == {} and llm.fast_model is None  # not on the server: ignored
    local_mod.reset_cache()
