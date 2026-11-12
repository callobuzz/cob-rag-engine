"""The financial year and gap-free document numbers (GST Rule 46: a unique number per year).

A number is `PREFIX/YY-YY/NNNNN` (`INV/26-27/00001`). `next_number` takes it from a counter row that
is locked in the caller's transaction, so a rollback gives the number back: nothing is skipped and
nothing is used twice. The issue date and the year are Indian time.
"""

from datetime import UTC, datetime
from typing import Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.db.models.tax_document import TaxDocumentCounter

IST: Final = ZoneInfo("Asia/Kolkata")
MAX_NUMBER_LENGTH: Final = 16  # Rule 46(b): at most sixteen characters

Series = Literal["invoice", "credit_note"]


class NumberTooLong(RAGKernelError):  # noqa: N818 - pinned name
    """A document number would exceed sixteen characters (a longer prefix or a huge year)."""


def financial_year(moment: datetime) -> str:
    """`26-27` for any moment from 1 April 2026 to 31 March 2027, Indian time."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    local = moment.astimezone(IST)
    start = local.year if local.month >= 4 else local.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def format_number(prefix: str, fy: str, seq: int) -> str:
    number = f"{prefix}/{fy}/{seq:05d}"
    if len(number) > MAX_NUMBER_LENGTH:
        raise NumberTooLong(f"document number {number!r} is longer than {MAX_NUMBER_LENGTH}")
    return number


async def next_number(
    session: AsyncSession, *, series: Series, prefix: str, fy: str
) -> tuple[int, str]:
    """Take the next number of (series, prefix, fy). Same transaction as the document itself.

    The counter row is created on first use, then locked until the transaction ends; a second
    caller waits, so two documents never get the same number and a rollback leaves no hole.
    """
    await session.execute(
        insert(TaxDocumentCounter)
        .values(series=series, prefix=prefix, fy=fy, last_seq=0)
        .on_conflict_do_nothing(index_elements=["series", "prefix", "fy"])
    )
    counter = (
        await session.execute(
            select(TaxDocumentCounter)
            .where(
                TaxDocumentCounter.series == series,
                TaxDocumentCounter.prefix == prefix,
                TaxDocumentCounter.fy == fy,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    counter.last_seq += 1
    await session.flush()
    return counter.last_seq, format_number(prefix, fy, counter.last_seq)
