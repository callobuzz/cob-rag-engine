# src/callobuzz_rag/conversation/gates.py
"""Gate 1 (design §5.2): the best dense cosine among the final chunks must reach a
threshold, or the turn is "no documented answer" whatever the model would write.

Dense cosine is the only absolute signal the pipeline keeps: the convex fusion score is
min-max normalised per query (the top chunk is always 1.0) and RRF scores are ~1/60.

Caveat: `dense_score` is the best cosine across every dense leg. The shipped thresholds are
measured with HyDE, classification and multi-query off (`benchmarks/run.py` BASE_RETRIEVAL);
a knowledge base with HyDE or query variants on takes a max over more legs (the HyDE leg
compares a hypothetical answer), so the gate is more permissive there.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from callobuzz_rag.conversation.schema import AssistantConfig
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.retrieval.models import ChunkScoreBreakdown

DEFAULTS_PATH = Path(__file__).parent / "data" / "gate_defaults.json"
ThresholdSource = Literal["corpus", "measured", "setting", "none"]


@dataclass(frozen=True)
class GateResult:
    passed: bool
    top_score: float | None
    threshold: float | None
    source: ThresholdSource
    chunks: int


@cache
def load_gate_defaults() -> dict[str, dict[str, Any]]:
    raw = json.loads(DEFAULTS_PATH.read_text("utf-8"))
    return dict(raw.get("models", {}))


def top_dense_score(score_breakdown: Mapping[str, "ChunkScoreBreakdown"]) -> float | None:
    scores = [b.dense_score for b in score_breakdown.values() if b.dense_score is not None]
    return max(scores) if scores else None


def threshold_for(
    config: "AssistantConfig", applied_embedding: dict[str, Any] | None, settings: "Settings"
) -> tuple[float | None, ThresholdSource]:
    if config.scope.min_retrieval_score is not None:
        return config.scope.min_retrieval_score, "corpus"
    model = (applied_embedding or {}).get("model")
    measured = load_gate_defaults().get(str(model)) if model else None
    if measured and isinstance(measured.get("threshold"), int | float):
        return float(measured["threshold"]), "measured"
    if settings.assistant_min_retrieval_score is not None:
        return settings.assistant_min_retrieval_score, "setting"
    return None, "none"


def retrieval_gate(
    *,
    score_breakdown: Mapping[str, "ChunkScoreBreakdown"],
    chunks: int,
    threshold: float | None,
    source: ThresholdSource,
) -> GateResult:
    top = top_dense_score(score_breakdown)
    if chunks == 0:
        return GateResult(False, top, threshold, source, 0)
    if threshold is None or top is None:  # no threshold, or a keyword-only search
        return GateResult(True, top, threshold, source, chunks)
    return GateResult(top >= threshold, top, threshold, source, chunks)
