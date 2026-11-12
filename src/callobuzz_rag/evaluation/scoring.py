"""Per-question RAGAS scores (conversation C7). Sync: callers use asyncio.to_thread."""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from callobuzz_rag.evaluation.metrics import (
    ANSWER_CORRECTNESS,
    ANSWER_RELEVANCY,
    CONTEXT_PRECISION,
    CONTEXT_RECALL,
    CONTEXT_RELEVANCE,
    FAITHFULNESS,
)
from callobuzz_rag.evaluation.ragas_bridge import RagasEmbeddings, build_ragas_llm

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.core.config_resolver import ResolvedLLMConfig

# RAGAS row keys -> engine metric names. Installed ragas 0.2.15 names FactualCorrectness
# "factual_correctness" and ContextRelevance "nv_context_relevance"; some 0.2.x builds put the
# scoring mode in the key ("factual_correctness(mode=f1)"), so both spellings map.
RAGAS_NAMES: dict[str, str] = {
    "faithfulness": FAITHFULNESS,
    "answer_relevancy": ANSWER_RELEVANCY,
    "llm_context_precision_without_reference": CONTEXT_PRECISION,
    "context_recall": CONTEXT_RECALL,
    "factual_correctness": ANSWER_CORRECTNESS,
    "factual_correctness(mode=f1)": ANSWER_CORRECTNESS,
    "nv_context_relevance": CONTEXT_RELEVANCE,
}
ALL_NAMES = list(dict.fromkeys(RAGAS_NAMES.values()))


@dataclass(frozen=True)
class ScoringSample:
    question: str
    answer: str
    contexts: list[str]
    reference: str | None


def _evaluate(dataset: Any, metrics: list[Any], **kwargs: Any) -> Any:
    from ragas import evaluate

    return evaluate(dataset=dataset, metrics=metrics, show_progress=False, **kwargs)


def _clean(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or math.isnan(float(value)):
        return None
    return float(value)


def score_samples(
    samples: list[ScoringSample],
    judge_config: "ResolvedLLMConfig",
    settings: "Settings",
    *,
    provider: Any = None,
    answer_only: bool = False,
    objectives_only: bool = False,
) -> list[dict[str, float | None]]:
    """One dict per sample, every engine metric present (None = not scored).

    `provider` is the judge's `LiteLLMProvider`: pass the one the caller also uses for criteria
    and failure reasons so a single cost ledger covers every judge call. Reference-needing
    metrics (context recall, answer correctness) run only on samples that have a reference.
    `objectives_only` scores faithfulness and answer relevancy and nothing else: the optimizer's
    two quality objectives, so a trial does not pay for metrics it never reads.
    """
    from callobuzz_rag.evaluation.optional import require_ragas

    require_ragas()

    from ragas import EvaluationDataset, SingleTurnSample
    from ragas.metrics import (
        ContextRelevance,
        FactualCorrectness,
        Faithfulness,
        LLMContextPrecisionWithoutReference,
        LLMContextRecall,
        ResponseRelevancy,
    )
    from ragas.run_config import RunConfig

    out: list[dict[str, float | None]] = [dict.fromkeys(ALL_NAMES) for _ in samples]
    groups = {
        True: [i for i, s in enumerate(samples) if s.reference],
        False: [i for i, s in enumerate(samples) if not s.reference],
    }
    llm = build_ragas_llm(judge_config, provider)
    with RagasEmbeddings(settings) as embeddings:
        for with_reference, indexes in groups.items():
            if not indexes or (answer_only and not with_reference):
                continue
            metrics: list[Any] = (
                [Faithfulness(), ResponseRelevancy()]
                if objectives_only
                else [FactualCorrectness()]
                if answer_only
                else [
                    Faithfulness(),
                    ResponseRelevancy(),
                    LLMContextPrecisionWithoutReference(),
                    ContextRelevance(),
                ]
            )
            if with_reference and not answer_only and not objectives_only:
                metrics += [LLMContextRecall(), FactualCorrectness()]
            dataset = EvaluationDataset(
                samples=[
                    SingleTurnSample(
                        user_input=samples[i].question,
                        response=samples[i].answer,
                        retrieved_contexts=samples[i].contexts or [""],
                        reference=samples[i].reference if with_reference else None,
                    )
                    for i in indexes
                ]
            )
            result = _evaluate(
                dataset,
                metrics,
                llm=llm,
                embeddings=embeddings.ragas,
                run_config=RunConfig(max_workers=4),
            )
            for i, row in zip(indexes, list(result.scores), strict=True):
                for key, value in row.items():
                    if key in RAGAS_NAMES:
                        out[i][RAGAS_NAMES[key]] = _clean(value)
    return out
