"""PIP1: the profile settings, their validation and the seeds."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from callobuzz_rag.tiers.schema import ProfileConfig, rule_text
from callobuzz_rag.tiers.seeds import SEEDS

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic/versions/phase16_001_pipeline_profiles.py"
)


@pytest.mark.parametrize(
    ("config", "rule"),
    [
        ({"answer_route": "local"}, "local"),  # needs overrides_kb_settings
        ({"reranker": "api"}, "rerank_model"),
        ({"language_models": {"xx": "m"}}, "language"),
        ({"top_k": 5, "final_k": 8}, "final_k"),
        ({"surprise": 1}, "extra"),  # StrictRequest
        ({"max_answer_tokens": 10}, "max_answer_tokens"),
        ({"priority": "urgent"}, "priority"),
    ],
)
def test_invalid_profiles_name_the_rule(config: dict, rule: str) -> None:
    with pytest.raises(ValidationError) as err:
        ProfileConfig.model_validate(config)
    assert rule in str(err.value)
    assert rule_text(err.value)


def test_local_message_is_a_sentence_a_deployer_understands() -> None:
    with pytest.raises(ValidationError) as err:
        ProfileConfig.model_validate({"answer_route": "local"})
    assert (
        "A profile that uses the local model must also set "
        "'Use these settings instead of each knowledge base's own'." in rule_text(err.value)
    )


def test_llm_reranker_may_follow_the_engine_model() -> None:
    # Premium seeds reranker "llm" with no model: it follows the knowledge base / engine.
    assert ProfileConfig.model_validate({"reranker": "llm"}).rerank_model is None


def test_language_map_none_follows_and_empty_disables() -> None:
    assert ProfileConfig().language_models is None
    assert ProfileConfig.model_validate({"language_models": {}}).language_models == {}
    # keys are canonicalised like the knowledge base's own map
    cfg = ProfileConfig.model_validate({"language_models": {"HI-latn": "google/gemma-4-31b-it"}})
    assert cfg.language_models == {"hi-Latn": "google/gemma-4-31b-it"}


def test_seeds_validate_and_standard_is_a_no_op() -> None:
    for key, seed in SEEDS.items():
        assert ProfileConfig.model_validate(seed["config"]), key
    assert ProfileConfig.model_validate(SEEDS["standard"]["config"]) == ProfileConfig()
    assert SEEDS["standard"]["is_default"] is True
    assert sum(1 for s in SEEDS.values() if s["is_default"]) == 1


def test_free_seed_is_the_cheap_local_profile() -> None:
    free = ProfileConfig.model_validate(SEEDS["free"]["config"])
    assert (free.answer_route, free.reranker, free.priority) == ("local", "off", "low")
    assert free.overrides_kb_settings and not free.followups
    assert (free.multi_query_count, free.max_answer_tokens, free.max_concurrency) == (0, 600, 2)
    assert SEEDS["free"]["label"] == "Economy"


def _migration_seeds() -> list[tuple[str, str, str, bool, dict]]:
    """The `_SEEDS` literal in the migration, evaluated without importing alembic."""
    source = MIGRATION.read_text(encoding="utf-8")
    block = re.search(r"^_SEEDS = (\(.*?^\))\n", source, re.S | re.M)
    assert block, "the migration lost its _SEEDS literal"
    return eval(block.group(1), {})


def test_migration_copies_the_seeds() -> None:
    migrated = {row[0]: row for row in _migration_seeds()}
    assert set(migrated) == set(SEEDS)
    for key, (_k, name, label, description, is_default, config) in migrated.items():
        seed = SEEDS[key]
        assert (name, label, description, is_default, config) == (
            seed["name"],
            seed["label"],
            seed["description"],
            seed["is_default"],
            seed["config"],
        )
