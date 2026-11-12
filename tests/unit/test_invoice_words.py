"""P2 T5: the amount in words, Indian numbering (lakh, crore)."""

import pytest

from callobuzz_rag.billing.invoicing.words import amount_in_words


@pytest.mark.parametrize(
    ("minor", "words"),
    [
        (117_882, "One Thousand One Hundred Seventy-Eight Rupees and Eighty-Two Paise Only"),
        (118_000, "One Thousand One Hundred Eighty Rupees Only"),
        (11_800_000, "One Lakh Eighteen Thousand Rupees Only"),
        (
            1_234_567_801,
            "One Crore Twenty-Three Lakh Forty-Five Thousand Six Hundred Seventy-Eight "
            "Rupees and One Paisa Only",
        ),
        (0, "Zero Rupees Only"),
        (100, "One Rupee Only"),
        (5, "Five Paise Only"),
        (99, "Ninety-Nine Paise Only"),
        (10_000_000, "One Lakh Rupees Only"),
        (1_000_000_000, "One Crore Rupees Only"),
        (100_000_000_000, "One Hundred Crore Rupees Only"),
        (2_000_000, "Twenty Thousand Rupees Only"),
        (110_000, "One Thousand One Hundred Rupees Only"),
    ],
)
def test_amount_in_words(minor: int, words: str) -> None:
    assert amount_in_words(minor) == words


def test_negative_amounts_are_written_as_their_size() -> None:
    assert amount_in_words(-59_000) == amount_in_words(59_000)
