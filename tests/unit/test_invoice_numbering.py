"""P2 T2: the financial year (April to March, IST) and the number format."""

from datetime import UTC, datetime

import pytest

from callobuzz_rag.billing.invoicing.numbering import NumberTooLong, financial_year, format_number


@pytest.mark.parametrize(
    ("utc", "fy"),
    [
        (datetime(2026, 10, 8, 6, 0, tzinfo=UTC), "26-27"),
        (datetime(2027, 3, 31, 18, 29, tzinfo=UTC), "26-27"),  # 23:59 IST on 31 Mar
        (datetime(2027, 3, 31, 18, 30, tzinfo=UTC), "27-28"),  # 00:00 IST on 1 Apr
        (datetime(2099, 4, 1, 0, 0, tzinfo=UTC), "99-00"),
    ],
)
def test_financial_year_is_april_to_march_in_ist(utc: datetime, fy: str) -> None:
    assert financial_year(utc) == fy


def test_naive_moment_is_read_as_utc() -> None:
    assert financial_year(datetime(2027, 3, 31, 18, 30)) == "27-28"


def test_format_number() -> None:
    assert format_number("INV", "26-27", 1) == "INV/26-27/00001"
    assert format_number("CN", "26-27", 123_456) == "CN/26-27/123456"
    assert len(format_number("ABCD", "26-27", 99_999)) == 16
    with pytest.raises(NumberTooLong):
        format_number("ABCD", "26-27", 100_000)  # 17 characters
