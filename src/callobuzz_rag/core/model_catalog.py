"""LLM model catalog — curated entries + optional OpenRouter live discovery.

The catalog is a static list of recommended models with metadata.
Models are identified by LiteLLM-compatible strings (provider/model-name).
OpenRouter models get the `openrouter/` prefix automatically.

Usage:
    from callobuzz_rag.core.model_catalog import MODEL_CATALOG, get_models_by_category

    budget_models = get_models_by_category("budget")
    all_free = get_models_by_category("free")
"""

import logging
from typing import TypedDict

import httpx

logger = logging.getLogger(__name__)


class ModelCatalogEntry(TypedDict, total=False):
    """Type-safe model catalog entry."""

    id: str
    name: str
    provider: str
    category: str  # "free" | "budget" | "standard" | "premium"
    input_cost_per_1m: float
    output_cost_per_1m: float
    context_window: int
    supports_streaming: bool
    recommended: bool


# Pricing categories:
#   free     - $0 input/output (community/free-tier models)
#   budget   - <$1/M tokens (DeepSeek, Qwen, etc.)
#   standard - $1-$10/M tokens (GPT-4.1 mini, Claude Haiku, etc.)
#   premium  - >$10/M tokens (GPT-4.1, Claude Opus, etc.)

MODEL_CATALOG: list[ModelCatalogEntry] = [
    # --- Free ---
    {
        "id": "openrouter/meta-llama/llama-4-scout:free",
        "name": "Llama 4 Scout (Free)",
        "provider": "openrouter",
        "category": "free",
        "input_cost_per_1m": 0.0,
        "output_cost_per_1m": 0.0,
        "context_window": 131072,
        "supports_streaming": True,
        "recommended": False,
    },
    {
        "id": "openrouter/qwen/qwen3-32b:free",
        "name": "Qwen3 32B (Free)",
        "provider": "openrouter",
        "category": "free",
        "input_cost_per_1m": 0.0,
        "output_cost_per_1m": 0.0,
        "context_window": 131072,
        "supports_streaming": True,
        "recommended": True,
    },
    # --- Budget ---
    {
        "id": "openrouter/deepseek/deepseek-v3.2",
        "name": "DeepSeek V3.2",
        "provider": "openrouter",
        "category": "budget",
        "input_cost_per_1m": 0.14,
        "output_cost_per_1m": 0.28,
        "context_window": 131072,
        "supports_streaming": True,
        "recommended": True,
    },
    {
        "id": "openrouter/qwen/qwen3.5-flash-02-23",
        "name": "Qwen 3.5 Flash",
        "provider": "openrouter",
        "category": "budget",
        "input_cost_per_1m": 0.07,
        "output_cost_per_1m": 0.30,
        "context_window": 131072,
        "supports_streaming": True,
        "recommended": False,
    },
    {
        "id": "deepseek/deepseek-chat",
        "name": "DeepSeek Chat (Direct)",
        "provider": "deepseek",
        "category": "budget",
        "input_cost_per_1m": 0.14,
        "output_cost_per_1m": 0.28,
        "context_window": 131072,
        "supports_streaming": True,
        "recommended": False,
    },
    # --- Standard ---
    {
        "id": "openai/gpt-4.1-nano",
        "name": "GPT-4.1 Nano",
        "provider": "openai",
        "category": "standard",
        "input_cost_per_1m": 0.10,
        "output_cost_per_1m": 0.40,
        "context_window": 1047576,
        "supports_streaming": True,
        "recommended": False,
    },
    {
        "id": "openai/gpt-4.1-mini",
        "name": "GPT-4.1 Mini",
        "provider": "openai",
        "category": "standard",
        "input_cost_per_1m": 0.40,
        "output_cost_per_1m": 1.60,
        "context_window": 1047576,
        "supports_streaming": True,
        "recommended": True,
    },
    {
        "id": "anthropic/claude-haiku-4-5-20251001",
        "name": "Claude 4.5 Haiku",
        "provider": "anthropic",
        "category": "standard",
        "input_cost_per_1m": 0.80,
        "output_cost_per_1m": 4.00,
        "context_window": 200000,
        "supports_streaming": True,
        "recommended": False,
    },
    {
        "id": "google/gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
        "provider": "google",
        "category": "standard",
        "input_cost_per_1m": 0.15,
        "output_cost_per_1m": 0.60,
        "context_window": 1048576,
        "supports_streaming": True,
        "recommended": False,
    },
    # --- Premium ---
    {
        "id": "openai/gpt-4.1",
        "name": "GPT-4.1",
        "provider": "openai",
        "category": "premium",
        "input_cost_per_1m": 2.00,
        "output_cost_per_1m": 8.00,
        "context_window": 1047576,
        "supports_streaming": True,
        "recommended": False,
    },
    {
        "id": "anthropic/claude-sonnet-4-20250514",
        "name": "Claude Sonnet 4",
        "provider": "anthropic",
        "category": "premium",
        "input_cost_per_1m": 3.00,
        "output_cost_per_1m": 15.00,
        "context_window": 200000,
        "supports_streaming": True,
        "recommended": True,
    },
    {
        "id": "google/gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "provider": "google",
        "category": "premium",
        "input_cost_per_1m": 1.25,
        "output_cost_per_1m": 10.00,
        "context_window": 1048576,
        "supports_streaming": True,
        "recommended": False,
    },
]


def get_models_by_category(category: str) -> list[ModelCatalogEntry]:
    """Filter catalog by pricing category."""
    return [m for m in MODEL_CATALOG if m["category"] == category]


# ---------------------------------------------------------------------------
# Embedding model catalog — validated set of supported embedding models.
# ---------------------------------------------------------------------------


class EmbeddingCatalogEntry(TypedDict, total=False):
    """Type-safe embedding catalog entry."""

    id: str
    name: str
    provider: str  # "local" | "tei" | "jina" | "litellm"
    model: str  # canonical model string — used for all stored comparisons
    dimensions: int
    notes: str
    # True only when the provider's own documentation lists Hindi or 100+ languages. Absent
    # when the documentation does not say (the dashboard then shows no warning either way).
    multilingual: bool
    multilingual_source: str  # the documentation page the flag was read from


EMBEDDING_CATALOG: list[EmbeddingCatalogEntry] = [
    {
        "id": "bge-m3-local",
        "name": "BGE-M3 (Local)",
        "provider": "local",
        "model": "BAAI/bge-m3",
        "dimensions": 1024,
        "notes": "Fast local embedding, no API key required",
        "multilingual": True,
        "multilingual_source": "https://huggingface.co/BAAI/bge-m3",
    },
    {
        "id": "bge-m3-tei",
        "name": "BGE-M3 (TEI)",
        "provider": "tei",
        "model": "BAAI/bge-m3",
        "dimensions": 1024,
        "notes": "Containerized TEI service; the engine default (RAG_TEI_MODEL_ID in docker-compose.yml)",
        "multilingual": True,
        "multilingual_source": "https://huggingface.co/BAAI/bge-m3",
    },
    {
        "id": "bge-base-en-v1.5-local",
        "name": "BGE-Base EN v1.5 (Local)",
        "provider": "local",
        "model": "BAAI/bge-base-en-v1.5",
        "dimensions": 768,
        "notes": "English-only, smaller and faster than BGE-M3, no API key required",
        "multilingual": False,
        "multilingual_source": "https://huggingface.co/BAAI/bge-base-en-v1.5",
    },
    {
        "id": "bge-base-en-v1.5-tei",
        "name": "BGE-Base EN v1.5 (TEI)",
        "provider": "tei",
        "model": "BAAI/bge-base-en-v1.5",
        "dimensions": 768,
        "notes": "English-only; the TEI container's default before BGE-M3 (set RAG_TEI_MODEL_ID to keep it)",
        "multilingual": False,
        "multilingual_source": "https://huggingface.co/BAAI/bge-base-en-v1.5",
    },
    {
        "id": "jina-v3",
        "name": "Jina Embeddings v3",
        "provider": "jina",
        "model": "jina-embeddings-v3",
        "dimensions": 1024,
        "notes": "API-based, Matryoshka support, multilingual",
        "multilingual": True,
        "multilingual_source": "https://docs.jina.ai",
    },
    # --- Hosted models through LiteLLM (RAG_EMBEDDING_PROVIDER=litellm) ---
    # Any other LiteLLM embedding model also works as the engine default: set
    # RAG_EMBEDDING_MODEL and RAG_EMBEDDING_DIMENSIONS and it is accepted as-is.
    {
        "id": "openai-text-embedding-3-small",
        "name": "OpenAI text-embedding-3-small",
        "provider": "litellm",
        "model": "openai/text-embedding-3-small",
        "dimensions": 1536,
        "notes": "Hosted, cheap, strong general-purpose English/multilingual",
    },
    {
        "id": "openai-text-embedding-3-large",
        "name": "OpenAI text-embedding-3-large",
        "provider": "litellm",
        "model": "openai/text-embedding-3-large",
        "dimensions": 3072,
        "notes": "Hosted, highest-quality OpenAI embedding",
    },
    {
        "id": "cohere-embed-english-v3",
        "name": "Cohere Embed English v3",
        "provider": "litellm",
        "model": "cohere/embed-english-v3.0",
        "dimensions": 1024,
        "notes": "Hosted, separate document/query encodings",
        "multilingual": False,
        "multilingual_source": "https://docs.cohere.com/docs/cohere-embed",
    },
    {
        "id": "cohere-embed-multilingual-v3",
        "name": "Cohere Embed Multilingual v3",
        "provider": "litellm",
        "model": "cohere/embed-multilingual-v3.0",
        "dimensions": 1024,
        "notes": "Hosted, 100+ languages",
        "multilingual": True,
        "multilingual_source": "https://docs.cohere.com/docs/cohere-embed",
    },
    {
        "id": "voyage-3",
        "name": "Voyage 3",
        "provider": "litellm",
        "model": "voyage/voyage-3",
        "dimensions": 1024,
        "notes": "Hosted, retrieval-optimised",
        "multilingual": True,
        "multilingual_source": "https://docs.voyageai.com/docs/embeddings",
    },
    {
        "id": "mistral-embed",
        "name": "Mistral Embed",
        "provider": "litellm",
        "model": "mistral/mistral-embed",
        "dimensions": 1024,
        "notes": "Hosted",
        "multilingual": False,
        "multilingual_source": "https://docs.mistral.ai/capabilities/embeddings/",
    },
    {
        "id": "gemini-text-embedding-004",
        "name": "Google text-embedding-004",
        "provider": "litellm",
        "model": "gemini/text-embedding-004",
        "dimensions": 768,
        "notes": "Hosted (Gemini API)",
        "multilingual": False,
        "multilingual_source": "https://ai.google.dev/gemini-api/docs/embeddings",
    },
]


def get_embedding_catalog_entry(
    model: str, provider: str | None = None
) -> EmbeddingCatalogEntry | None:
    """Look up an embedding catalog entry by model string and optional provider."""
    for entry in EMBEDDING_CATALOG:
        if entry["model"] == model and (provider is None or entry["provider"] == provider):
            return entry
    return None


def get_embedding_entry_by_id(entry_id: str) -> EmbeddingCatalogEntry | None:
    """Look up an embedding catalog entry by its ID."""
    for entry in EMBEDDING_CATALOG:
        if entry["id"] == entry_id:
            return entry
    return None


def get_model_by_id(model_id: str) -> ModelCatalogEntry | None:
    """Look up a model by its LiteLLM ID string."""
    for m in MODEL_CATALOG:
        if m["id"] == model_id:
            return m
    return None


def _categorize_price(input_per_1m: float) -> str:
    """Categorize a model by its input pricing."""
    if input_per_1m < 0.001:
        return "free"
    if input_per_1m < 1.0:
        return "budget"
    if input_per_1m < 10.0:
        return "standard"
    return "premium"


async def discover_openrouter_models(api_key: str) -> list[ModelCatalogEntry]:
    """Discover available models from OpenRouter's /api/v1/models endpoint.

    Filters to text-only models (text->text modality).
    Returns list of model dicts in the same format as MODEL_CATALOG entries.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        data = response.json()

    models: list[ModelCatalogEntry] = []
    for item in data.get("data", []):
        # Filter: text-only models
        modality = (item.get("architecture") or {}).get("modality", "")
        if modality != "text->text":
            continue

        pricing = item.get("pricing", {})
        input_cost = float(pricing.get("prompt", 0)) * 1_000_000
        output_cost = float(pricing.get("completion", 0)) * 1_000_000

        models.append(
            {
                "id": f"openrouter/{item['id']}",
                "name": item.get("name", item["id"]),
                "provider": "openrouter",
                "category": _categorize_price(input_cost),
                "input_cost_per_1m": round(input_cost, 4),
                "output_cost_per_1m": round(output_cost, 4),
                "context_window": item.get("context_length", 0),
                "supports_streaming": True,
            }
        )

    return models
