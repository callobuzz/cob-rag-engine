"""Tests for LLM model catalog."""

import pytest


def test_catalog_not_empty():
    """Catalog must contain at least one model."""
    from callobuzz_rag.core.model_catalog import MODEL_CATALOG

    assert len(MODEL_CATALOG) > 0


def test_catalog_entry_structure():
    """Each catalog entry must have required fields."""
    from callobuzz_rag.core.model_catalog import MODEL_CATALOG

    required_fields = {
        "id",
        "name",
        "provider",
        "category",
        "input_cost_per_1m",
        "output_cost_per_1m",
        "context_window",
        "supports_streaming",
    }
    for entry in MODEL_CATALOG:
        for field in required_fields:
            assert field in entry, f"Entry '{entry.get('id', '?')}' missing field '{field}'"


def test_catalog_has_all_categories():
    """Catalog should cover all pricing tiers."""
    from callobuzz_rag.core.model_catalog import MODEL_CATALOG

    categories = {e["category"] for e in MODEL_CATALOG}
    assert "free" in categories
    assert "budget" in categories
    assert "standard" in categories
    assert "premium" in categories


def test_catalog_has_recommended():
    """At least one model should be marked as recommended."""
    from callobuzz_rag.core.model_catalog import MODEL_CATALOG

    recommended = [e for e in MODEL_CATALOG if e.get("recommended")]
    assert len(recommended) >= 1


def test_get_models_by_category():
    """get_models_by_category returns filtered list."""
    from callobuzz_rag.core.model_catalog import get_models_by_category

    budget = get_models_by_category("budget")
    assert all(m["category"] == "budget" for m in budget)


def test_get_models_by_category_unknown_returns_empty():
    """Unknown category returns empty list."""
    from callobuzz_rag.core.model_catalog import get_models_by_category

    result = get_models_by_category("mythical")
    assert result == []


def test_get_model_by_id_found():
    """get_model_by_id returns the model dict when found."""
    from callobuzz_rag.core.model_catalog import MODEL_CATALOG, get_model_by_id

    first = MODEL_CATALOG[0]
    result = get_model_by_id(first["id"])
    assert result is not None
    assert result["id"] == first["id"]


def test_get_model_by_id_not_found():
    """get_model_by_id returns None for unknown model."""
    from callobuzz_rag.core.model_catalog import get_model_by_id

    result = get_model_by_id("nonexistent/model")
    assert result is None


@pytest.mark.asyncio
async def test_discover_openrouter_models_with_mock(monkeypatch):
    """discover_openrouter_models should parse OpenRouter API response."""
    import httpx

    from callobuzz_rag.core.model_catalog import discover_openrouter_models

    mock_response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://openrouter.ai/api/v1/models"),
        json={
            "data": [
                {
                    "id": "deepseek/deepseek-v3.2",
                    "name": "DeepSeek V3.2",
                    "pricing": {"prompt": "0.00000014", "completion": "0.00000028"},
                    "context_length": 131072,
                    "architecture": {"modality": "text->text"},
                },
                {
                    "id": "meta/llama-3-8b",
                    "name": "Llama 3 8B",
                    "pricing": {"prompt": "0", "completion": "0"},
                    "context_length": 8192,
                    "architecture": {"modality": "text->text"},
                },
                {
                    "id": "openai/dall-e-3",
                    "name": "DALL-E 3",
                    "pricing": {"prompt": "0.01", "completion": "0.01"},
                    "context_length": 4096,
                    "architecture": {"modality": "text->image"},
                },
            ]
        },
    )

    async def mock_get(*args, **kwargs):
        return mock_response

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    models = await discover_openrouter_models("fake-api-key")
    # Should filter out image models (text->image)
    assert len(models) == 2
    assert models[0]["id"] == "openrouter/deepseek/deepseek-v3.2"
    assert models[1]["category"] == "free"  # pricing == 0
