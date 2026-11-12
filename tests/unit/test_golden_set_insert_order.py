"""A new golden set is flushed before its pairs are added.

Pairs reference the set by foreign key but no relationship orders the inserts, so adding both
in one flush violated `golden_pair_set_id_fkey` on a real database (mocked sessions hid it).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.api.schemas.evals import GoldenPairFields
from callobuzz_rag.evaluation.golden_runner import create_golden_set


async def test_create_golden_set_flushes_the_set_before_adding_pairs() -> None:
    events: list[str] = []
    session = AsyncMock()
    session.add = MagicMock(side_effect=lambda obj: events.append(f"add:{type(obj).__name__}"))
    session.flush = AsyncMock(side_effect=lambda: events.append("flush"))
    session.commit = AsyncMock(side_effect=lambda: events.append("commit"))

    await create_golden_set(
        session,
        uuid.uuid4(),
        name="n",
        source="owner",
        pairs=[GoldenPairFields(query="q", expected_answer="a")],
    )

    assert events == ["add:GoldenSet", "flush", "add:GoldenPair", "commit"]
