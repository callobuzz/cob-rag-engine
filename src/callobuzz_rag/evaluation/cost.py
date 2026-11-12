"""What a run will cost, before it runs (conversation C7).

An estimate, never a promise: the token counts below are assumptions, and the judge-call count
per question is a floor (RAGAS metrics make several calls each; the measured figure replaces
the assumption once a live run has recorded it). A model with no known price is listed in
`unpriced_models` instead of being priced as free.
"""

from dataclasses import dataclass, field

from callobuzz_rag.analytics.cost_tracker import CostTracker
from callobuzz_rag.llm.cost import priced_cost

ANSWER_PROMPT_TOKENS = 2500
ANSWER_COMPLETION_TOKENS = 300
JUDGE_PROMPT_TOKENS = 1800
JUDGE_COMPLETION_TOKENS = 250


@dataclass(frozen=True)
class CostEstimate:
    questions: int
    answer_usd: float
    judge_usd: float
    total_usd: float
    unpriced_models: list[str] = field(default_factory=list)
    assumptions: dict[str, int] = field(default_factory=dict)


def _price(
    model: str,
    provider: str | None,
    prompt: int,
    completion: int,
    tracker: CostTracker | None,
    unpriced: list[str],
) -> float:
    cost: float | None
    if tracker is not None and tracker.has_price(model):
        cost = tracker.estimate_cost(model, prompt, completion)
    else:
        cost = priced_cost(model, provider, prompt, completion)
    if cost is None:
        if model not in unpriced:
            unpriced.append(model)
        return 0.0
    return cost


def estimate_run_cost(
    questions: int,
    mode: str,
    answer_model: str,
    judge_model: str,
    metric_count: int,
    criteria_count: int,
    tracker: CostTracker | None = None,
    *,
    answer_provider: str | None = None,
    judge_provider: str | None = None,
) -> CostEstimate:
    judge_calls = metric_count + (1 if criteria_count else 0)
    assumptions = {
        "answer_prompt_tokens": ANSWER_PROMPT_TOKENS,
        "answer_completion_tokens": ANSWER_COMPLETION_TOKENS,
        "judge_prompt_tokens": JUDGE_PROMPT_TOKENS,
        "judge_completion_tokens": JUDGE_COMPLETION_TOKENS,
        "judge_calls_per_question": judge_calls,
    }
    if mode != "full" or questions == 0:
        return CostEstimate(questions, 0.0, 0.0, 0.0, [], assumptions)
    unpriced: list[str] = []
    answer = questions * _price(
        answer_model,
        answer_provider,
        ANSWER_PROMPT_TOKENS,
        ANSWER_COMPLETION_TOKENS,
        tracker,
        unpriced,
    )
    judge = (
        questions
        * judge_calls
        * _price(
            judge_model,
            judge_provider,
            JUDGE_PROMPT_TOKENS,
            JUDGE_COMPLETION_TOKENS,
            tracker,
            unpriced,
        )
    )
    return CostEstimate(questions, answer, judge, answer + judge, unpriced, assumptions)
