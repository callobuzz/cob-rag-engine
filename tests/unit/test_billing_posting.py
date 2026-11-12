"""The pure side of posting: batches, the carried remainder, the customer's line (BIL3)."""

import pytest

from callobuzz_rag.billing.money import MICRO_PER_MINOR
from callobuzz_rag.billing.posting import plan_posting


def test_three_small_batches_carry_until_a_whole_minor_unit() -> None:
    residue = 0
    minors = []
    for _ in range(3):
        plan = plan_posting([3_334], 1, residue)
        minors.append(plan.minor)
        residue = plan.residue
    assert minors == [0, 0, 1] and residue == 2
    assert plan_posting([1], 1, 9_999).minor == 1  # 9,999 carried + 1 -> one unit, nothing left
    assert plan_posting([1], 1, 9_999).residue == 0


def test_an_empty_batch_writes_nothing() -> None:
    plan = plan_posting([], 0, 0)
    assert plan.minor == 0 and plan.residue == 0


def test_a_batch_below_one_minor_unit_writes_no_entry() -> None:
    plan = plan_posting([9_000], 1, 0)
    assert plan.minor == 0 and plan.residue == 9_000


@pytest.mark.parametrize(
    ("questions", "line"),
    [(1, "Usage: 1 answer"), (3, "Usage: 3 answers"), (0, "Usage: assistant tasks")],
)
def test_the_line_the_customer_reads(questions: int, line: str) -> None:
    assert plan_posting([MICRO_PER_MINOR], questions, 0).memo_public == line


async def test_posting_takes_only_charges_in_the_accounts_currency() -> None:
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from sqlalchemy.dialects import postgresql

    from callobuzz_rag.billing import posting

    account = SimpleNamespace(currency="USD", usage_residue_micro=0)
    statements: list[str] = []

    async def execute(stmt):  # type: ignore[no-untyped-def]
        statements.append(str(stmt.compile(dialect=postgresql.dialect())))
        result = MagicMock()
        result.scalar_one_or_none.return_value = account
        result.scalars.return_value.all.return_value = []
        result.scalar_one.return_value = 2
        return result

    session = AsyncMock()
    session.execute = execute
    session.__aenter__.return_value = session
    assert await posting.post_account(lambda: session, uuid.uuid4()) is None
    assert "usage_charges.currency = %(currency_1)s" in statements[1]
    assert "FOR NO KEY UPDATE" in statements[0]  # no clash with the charge inserts' FK share lock
    assert "usage_charges.currency != %(currency_1)s" in statements[2]  # strays are reported
