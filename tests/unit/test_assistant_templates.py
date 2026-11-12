"""The eight business templates: valid configs, ten tests each, no model ids, no US data."""

import re

import pytest

from callobuzz_rag.conversation.templates import TEMPLATE_KEYS, list_templates, load_template

EXPECTED = {
    "generic_support",
    "clinic",
    "store",
    "coaching",
    "saas",
    "real_estate",
    "government",
    "restaurant",
}


def test_all_eight_exist() -> None:
    assert set(TEMPLATE_KEYS) == EXPECTED
    assert [t.key for t in list_templates()] == list(TEMPLATE_KEYS)


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_template_is_complete(key: str) -> None:
    t = load_template(key)
    c = t.config
    assert c.identity.business_type == key
    assert len(t.tests) == 10
    kinds = [x.expected for x in t.tests]
    assert {"answer", "decline", "smalltalk", "no_answer"} <= set(kinds)
    assert kinds.count("answer") >= 2 and kinds.count("smalltalk") >= 1
    assert "lead" in kinds or "handoff" in kinds
    assert any(x.language == "hi-Latn" for x in t.tests)
    assert 1 <= len(c.conversation.starters) <= 4
    assert len(c.scope.topics) >= 4
    assert 8 <= len(c.rules) <= 12
    assert sum(r.enabled for r in c.rules) <= 30
    assert all(r.source == "template" for r in c.rules)
    topic_ids = {tp.id for tp in c.scope.topics}
    for rule in c.rules:
        if rule.when and rule.when.topic:
            assert rule.when.topic in topic_ids
    for tid in c.leads.triggers.topics:
        assert tid in topic_ids


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_no_model_ids_and_no_us_data(key: str) -> None:
    raw = load_template(key).model_dump_json()
    assert not re.search(r"gpt-|claude|llama|gemini|mistral|openrouter", raw, re.I)
    assert "$" not in raw and "ZIP" not in raw and "Acme" not in raw


def test_profiles() -> None:
    assert load_template("clinic").config.scope.sensitive_profile == "medical"
    assert load_template("government").config.scope.scope_mode == "strict_docs"
    assert load_template("coaching").config.scope.sensitive_profile == "education"
    assert load_template("generic_support").config.scope.sensitive_profile == "none"


def test_unknown_key() -> None:
    with pytest.raises(KeyError):
        load_template("bakery")


def test_migration_frozen_copy_still_validates_and_matches_today() -> None:
    import importlib.util
    from pathlib import Path

    from callobuzz_rag.conversation.schema import AssistantConfig

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "phase12_001_assistant_configs.py"
    )
    spec = importlib.util.spec_from_file_location("phase12_001", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frozen = AssistantConfig.model_validate(module.GENERIC_SUPPORT_V1)
    assert frozen == load_template("generic_support").config
