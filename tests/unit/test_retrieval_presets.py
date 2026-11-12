"""Tests for retrieval presets and bounds validation."""

import pytest


def test_presets_exist():
    """Three named presets must exist."""
    from callobuzz_rag.core.retrieval_presets import PRESETS

    assert "economy" in PRESETS
    assert "standard" in PRESETS
    assert "premium" in PRESETS


def test_preset_has_all_keys():
    """Each preset must define all configurable retrieval fields."""
    from callobuzz_rag.core.retrieval_presets import PRESETS, RETRIEVAL_FIELDS

    for name, preset in PRESETS.items():
        for field in RETRIEVAL_FIELDS:
            assert field in preset, f"Preset '{name}' missing field '{field}'"


def test_economy_is_cheapest():
    """Economy preset should disable expensive features."""
    from callobuzz_rag.core.retrieval_presets import PRESETS

    eco = PRESETS["economy"]
    assert eco["validation_enabled"] is False
    assert eco["hyde_enabled"] is False
    assert eco["query_classification_enabled"] is False
    assert eco["multi_query_count"] == 1


def test_premium_enables_all():
    """Premium preset should enable all quality features."""
    from callobuzz_rag.core.retrieval_presets import PRESETS

    prem = PRESETS["premium"]
    assert prem["validation_enabled"] is True
    assert prem["mmr_enabled"] is True
    assert prem["reranking_enabled"] is True


def test_validate_config_within_bounds():
    """Config within bounds should pass validation."""
    from callobuzz_rag.core.retrieval_presets import validate_retrieval_config

    config = {"top_k": 10, "mmr_lambda": 0.5}
    errors = validate_retrieval_config(config)
    assert errors == []


def test_validate_config_out_of_bounds():
    """Config outside bounds should return error messages."""
    from callobuzz_rag.core.retrieval_presets import validate_retrieval_config

    config = {"top_k": 500, "mmr_lambda": 5.0}
    errors = validate_retrieval_config(config)
    assert len(errors) == 2
    assert any("top_k" in e for e in errors)
    assert any("mmr_lambda" in e for e in errors)


def test_validate_config_unknown_keys_ignored():
    """Unknown keys should be silently ignored (forward-compat)."""
    from callobuzz_rag.core.retrieval_presets import validate_retrieval_config

    config = {"top_k": 10, "future_setting": True}
    errors = validate_retrieval_config(config)
    assert errors == []


def test_expand_preset_returns_full_config():
    """expand_preset returns a complete config dict from a preset name."""
    from callobuzz_rag.core.retrieval_presets import expand_preset

    config = expand_preset("economy")
    assert isinstance(config, dict)
    assert "top_k" in config
    assert "validation_enabled" in config


def test_expand_preset_unknown_raises():
    """expand_preset with unknown name should raise ValueError."""
    from callobuzz_rag.core.retrieval_presets import expand_preset

    with pytest.raises(ValueError, match="Unknown preset"):
        expand_preset("legendary")
