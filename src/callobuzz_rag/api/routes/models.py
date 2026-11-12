"""Model catalog & preset discovery endpoints."""

import logging

from fastapi import APIRouter, Header, HTTPException, Query, Request

from callobuzz_rag.api.schemas.models import (
    AnswerBenchmarksResponse,
    BenchmarkSummaryResponse,
    FastBenchmarksResponse,
    ModelCatalogResponse,
    ModelEntry,
    PresetListResponse,
)
from callobuzz_rag.core.answer_benchmarks import load_answer_models
from callobuzz_rag.core.benchmark_results import load_fast_models, load_summary
from callobuzz_rag.core.local_benchmarks import load_local_benchmarks  # PIP2
from callobuzz_rag.core.model_catalog import MODEL_CATALOG, discover_openrouter_models
from callobuzz_rag.core.retrieval_presets import effective_presets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/models", tags=["models"])


@router.get("", response_model=ModelCatalogResponse)
async def list_models(
    category: str | None = Query(None, description="Filter by pricing category."),
) -> ModelCatalogResponse:
    """List available LLM models from the curated catalog.

    Optionally filter by category: free, budget, standard, premium.
    """
    models = MODEL_CATALOG
    if category:
        models = [m for m in models if m["category"] == category]

    return ModelCatalogResponse(
        models=[ModelEntry(**m) for m in models],
        count=len(models),
    )


@router.get("/fast-benchmarks", response_model=FastBenchmarksResponse)
async def fast_benchmarks() -> FastBenchmarksResponse:
    """The measured fast models for the conversation director's turn labels.

    One row per model (and pinned OpenRouter host): label accuracy overall and per language,
    business-question recall, reply language, fallbacks, label latency p50/p90/p99 and the
    billed cost per 1,000 labels, each bar passed or not, plus the recommendations (the
    default: the fastest passing model with p90 at or under 1 s, else the fastest passing
    one; the cheapest and the most accurate passing ones). Read-only, from
    benchmarks/results/fast-models.json; `available` is false when the file is absent.
    """
    data = load_fast_models()
    return FastBenchmarksResponse(available=data is not None, benchmarks=data)


@router.get("/answer-benchmarks", response_model=AnswerBenchmarksResponse)
async def answer_benchmarks() -> AnswerBenchmarksResponse:
    """The measured answer models: who writes the best grounded reply.

    One row per model and thinking level on the same frozen English, Hindi and Hinglish
    items: pass rate, correct declines of questions the sources cannot answer, tables,
    first-token latency, the billed cost per 1,000 answers (USD and INR) and a slice per
    language, each bar met or not, plus the recommendations (the default answer model, the
    Hindi and Hinglish default and budget picks, the fastest, cheapest and most accurate, and
    the best Hindi tone), computed from the measurements. Read-only, from
    benchmarks/results/answer-models.json; `available` is false when the file is absent.
    """
    data = load_answer_models()
    # PIP2 local
    return AnswerBenchmarksResponse(
        available=data is not None, benchmarks=data, local=load_local_benchmarks()
    )
    # /PIP2 local


@router.get("/benchmark-summary", response_model=BenchmarkSummaryResponse)
async def benchmark_summary() -> BenchmarkSummaryResponse:
    """Headline tables of the engine's other benchmarks (retrieval on BEIR, real documents,
    turn labels, time to first token), each with its date and a link to its method."""
    data = load_summary()
    return BenchmarkSummaryResponse(available=data is not None, summary=data)


@router.get("/presets", response_model=PresetListResponse)
async def list_presets(request: Request) -> PresetListResponse:
    """List available retrieval presets with the settings each would store on this engine.

    `reranking_enabled` is the effective value: the built-in re-ranker is about 10 s a
    question on a CPU, so Standard and Premium leave it off unless this engine has a GPU,
    re-ranking is enabled engine-wide (`RAG_RERANKING_ENABLED`) or the API re-ranker is
    chosen (`RAG_RERANK_PROVIDER=llm`).
    """
    presets = effective_presets(request.app.state.settings)
    return PresetListResponse(presets=presets, count=len(presets))


@router.post("/discover/openrouter", response_model=ModelCatalogResponse)
async def discover_openrouter(
    x_openrouter_key: str = Header(..., description="OpenRouter API key for model discovery."),
) -> ModelCatalogResponse:
    """Discover available models from OpenRouter.

    Pass your OpenRouter API key via the X-OpenRouter-Key header.
    Returns models filtered to text-only (text->text modality).
    """
    try:
        models = await discover_openrouter_models(x_openrouter_key)
    except Exception as e:
        logger.warning("OpenRouter model discovery failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Failed to discover models from OpenRouter",
        ) from e

    return ModelCatalogResponse(
        models=[ModelEntry(**m) for m in models],
        count=len(models),
    )
