"""Type definitions and constants for the Bayesian auto-optimizer.

Separated from optimizer.py to allow lightweight imports without pulling
in Optuna or async dependencies.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class SearchSpaceParam:
    """Definition of a single parameter in the search space."""

    low: float
    high: float
    type: str  # "int" or "float"


@dataclass
class ParetoConfig:
    """A Pareto-optimal parameter configuration with its metric scores."""

    params: dict[str, Any]
    metrics: dict[str, float]
    trial_number: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "metrics": self.metrics,
            "trial_number": self.trial_number,
        }


SEARCH_SPACE: dict[str, SearchSpaceParam] = {
    "retrieval_top_k_dense": SearchSpaceParam(low=10, high=100, type="int"),
    "retrieval_top_k_sparse": SearchSpaceParam(low=10, high=100, type="int"),
    "rrf_k": SearchSpaceParam(low=20, high=120, type="int"),
    "mmr_lambda": SearchSpaceParam(low=0.3, high=0.9, type="float"),
    "temporal_decay_half_life_days": SearchSpaceParam(low=7.0, high=90.0, type="float"),
    "rerank_top_n_input": SearchSpaceParam(low=20, high=100, type="int"),
    "rerank_top_k_output": SearchSpaceParam(low=3, high=20, type="int"),
}
