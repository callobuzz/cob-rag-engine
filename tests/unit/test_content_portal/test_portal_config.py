"""Tests for content_portal.config — 3-tier resolution + AI model fallback."""

from unittest.mock import MagicMock

from callobuzz_rag.content_portal.config import (
    PORTAL_CONFIG_KEYS,
    PORTAL_DEFAULTS,
    get_portal_ai_model,
    is_portal_enabled,
    resolve_portal_config,
)


def _make_settings(**overrides: object) -> MagicMock:
    """Create a mock Settings with portal defaults."""
    defaults = {
        "portal_default_workflow": "single_reviewer",
        "portal_ai_default_model": "",
        "portal_auto_review_after_ingest": False,
        "llm_model": "anthropic/claude-sonnet-4-20250514",
        "enable_content_portal": False,
    }
    defaults.update(overrides)
    settings = MagicMock()
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings


# ---------- resolve_portal_config ----------


class TestResolvePortalConfig:
    def test_defaults_when_corpus_none(self) -> None:
        settings = _make_settings()
        result = resolve_portal_config(None, settings)
        assert result["portal_workflow"] == "single_reviewer"
        assert result["portal_auto_stage"] is False
        assert result["portal_routing_rules"] == []
        assert result["portal_ai_model"] == ""
        assert result["portal_post_ingest_review"] is False

    def test_corpus_overrides_all(self) -> None:
        corpus = {
            "portal_workflow": "maker_checker",
            "portal_auto_stage": True,
            "portal_routing_rules": [{"type": "pdf", "action": "ocr"}],
            "portal_ai_model": "openai/gpt-4o",
            "portal_post_ingest_review": True,
        }
        settings = _make_settings()
        result = resolve_portal_config(corpus, settings)
        assert result["portal_workflow"] == "maker_checker"
        assert result["portal_auto_stage"] is True
        assert result["portal_routing_rules"] == [{"type": "pdf", "action": "ocr"}]
        assert result["portal_ai_model"] == "openai/gpt-4o"
        assert result["portal_post_ingest_review"] is True

    def test_global_settings_fallback(self) -> None:
        settings = _make_settings(
            portal_default_workflow="maker_checker",
            portal_ai_default_model="anthropic/claude-haiku",
            portal_auto_review_after_ingest=True,
        )
        result = resolve_portal_config({}, settings)
        assert result["portal_workflow"] == "maker_checker"
        assert result["portal_ai_model"] == "anthropic/claude-haiku"
        assert result["portal_post_ingest_review"] is True

    def test_corpus_beats_global(self) -> None:
        corpus = {"portal_workflow": "maker_checker"}
        settings = _make_settings(portal_default_workflow="single_reviewer")
        result = resolve_portal_config(corpus, settings)
        assert result["portal_workflow"] == "maker_checker"

    def test_empty_corpus_dict(self) -> None:
        settings = _make_settings()
        result = resolve_portal_config({}, settings)
        assert result["portal_workflow"] == "single_reviewer"

    def test_returns_five_keys(self) -> None:
        settings = _make_settings()
        result = resolve_portal_config(None, settings)
        assert set(result.keys()) == {
            "portal_workflow",
            "portal_auto_stage",
            "portal_routing_rules",
            "portal_ai_model",
            "portal_post_ingest_review",
        }


# ---------- get_portal_ai_model ----------


class TestGetPortalAIModel:
    def test_corpus_portal_model_wins(self) -> None:
        corpus = {"portal_ai_model": "openai/gpt-4o"}
        settings = _make_settings(portal_ai_default_model="anthropic/claude-haiku")
        assert get_portal_ai_model(corpus, settings) == "openai/gpt-4o"

    def test_global_portal_default_fallback(self) -> None:
        settings = _make_settings(portal_ai_default_model="anthropic/claude-haiku")
        assert get_portal_ai_model({}, settings) == "anthropic/claude-haiku"

    def test_corpus_llm_config_fallback(self) -> None:
        corpus = {"llm_config": {"model": "openai/gpt-4-turbo"}}
        settings = _make_settings()
        assert get_portal_ai_model(corpus, settings) == "openai/gpt-4-turbo"

    def test_global_llm_model_fallback(self) -> None:
        settings = _make_settings(llm_model="anthropic/claude-sonnet-4-20250514")
        assert get_portal_ai_model(None, settings) == "anthropic/claude-sonnet-4-20250514"

    def test_none_corpus_config(self) -> None:
        settings = _make_settings()
        result = get_portal_ai_model(None, settings)
        assert result == "anthropic/claude-sonnet-4-20250514"

    def test_invalid_llm_config_type(self) -> None:
        corpus = {"llm_config": "not-a-dict"}
        settings = _make_settings()
        # Should skip non-dict llm_config and fall back to global
        assert get_portal_ai_model(corpus, settings) == "anthropic/claude-sonnet-4-20250514"


# ---------- is_portal_enabled ----------


class TestIsPortalEnabled:
    def test_disabled_by_default(self) -> None:
        settings = _make_settings()
        assert is_portal_enabled(settings) is False

    def test_enabled(self) -> None:
        settings = _make_settings(enable_content_portal=True)
        assert is_portal_enabled(settings) is True


# ---------- Constants ----------


class TestConstants:
    def test_portal_defaults_keys(self) -> None:
        assert set(PORTAL_DEFAULTS.keys()) == PORTAL_CONFIG_KEYS
