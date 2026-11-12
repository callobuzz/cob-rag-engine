"""The administrator-only reply: when a customer asks about running the server.

A question about server settings, deploying or the admin key has an answer in COB's docs, but
only for the platform operator. For anyone else retrieval never sees those pages, so COB would
say it has no answer, which is misleading. Instead, after a turn that found nothing (or a
question that names server things), COB runs one *probe*: a dense-only search of the
administrator-only records. The probe's text is never read by a model or shown to anyone; only
whether something scored above the gate matters. A hit turns the reply into "that is managed by
your platform administrator".
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from callobuzz_rag.cob.types import Audience

DATA_PATH: Final = Path(__file__).parent / "data" / "admin_topics.json"
PROBE_TOP_K: Final = 3
DEFAULT_THRESHOLD: Final = 0.5
ADMIN_FILTER: Final = {"audience": [Audience.ADMIN.value]}


@lru_cache(maxsize=1)
def _patterns() -> list[re.Pattern[str]]:
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return [re.compile(p, re.IGNORECASE) for p in raw["patterns"]]


def mentions_admin_topic(message: str) -> bool:
    """True when the message names something only the platform administrator handles."""
    return any(p.search(message) for p in _patterns())


async def _best_score(orchestrator: Any, corpus_id: Any, query: str, audiences: list[str]) -> float:
    result = await orchestrator.run_retrieval_pipeline(
        query,
        corpus_id,
        top_k=PROBE_TOP_K,
        final_k=PROBE_TOP_K,
        enable_hyde=False,
        enable_classification=False,
        enable_mmr=False,
        enable_reranking=False,
        enable_hybrid=False,
        metadata_filters={"audience": audiences},
        context_budget=False,
    )
    return max((float(c.score) for c in result.assembled.cited_chunks), default=0.0)


async def admin_only_hit(
    orchestrator: Any, corpus_id: Any, query: str, *, threshold: float | None
) -> bool:
    """Whether the administrator-only docs hold something that matches `query` as well as an
    answer must (the turn's gate threshold, 0.5 when it had none). Dense search only, so a
    keyword-only match cannot pass; the text found is discarded here."""
    best = await _best_score(orchestrator, corpus_id, query, ADMIN_FILTER["audience"])
    return best >= (DEFAULT_THRESHOLD if threshold is None else threshold)


async def admin_outranks_customer(
    orchestrator: Any,
    corpus_id: Any,
    query: str,
    *,
    customer_audiences: list[str],
    threshold: float | None,
) -> bool:
    """`admin_only_hit`, and the administrator-only docs match better than the pages the
    person may read. A word like "compose" or "Postgres" can name a server thing or an
    ordinary customer question; the docs the person may read get the first say."""
    admin = await _best_score(orchestrator, corpus_id, query, ADMIN_FILTER["audience"])
    if admin < (DEFAULT_THRESHOLD if threshold is None else threshold):
        return False
    customer = await _best_score(orchestrator, corpus_id, query, customer_audiences)
    return admin > customer
