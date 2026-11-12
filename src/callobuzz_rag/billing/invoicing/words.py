"""An amount of rupees in words, the way Indian invoices write it (lakh, crore).

Pure and integer-only: `amount_in_words(117_882)` is "One Thousand One Hundred Seventy-Eight
Rupees and Eighty-Two Paise Only". A credit note's negative amount is written as its size.
"""

from typing import Final

_ONES: Final = [
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
_TENS: Final = [
    "_",
    "_",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
]


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def _number(n: int) -> str:
    """A whole number above zero in Indian grouping: crore, lakh, thousand, then the rest."""
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, rest = divmod(n, 1_000)
    if crore:
        parts.append(f"{_number(crore)} Crore")
    if lakh:
        parts.append(f"{_below_hundred(lakh)} Lakh")
    if thousand:
        parts.append(f"{_below_hundred(thousand)} Thousand")
    if rest:
        parts.append(_below_thousand(rest))
    return " ".join(parts)


def amount_in_words(minor: int) -> str:
    """Rupees and paise in words, ending in "Only"."""
    rupees, paise = divmod(abs(minor), 100)
    parts = []
    if rupees or not paise:
        parts.append(
            f"{_number(rupees) if rupees else 'Zero'} {'Rupee' if rupees == 1 else 'Rupees'}"
        )
    if paise:
        parts.append(f"{_below_hundred(paise)} {'Paisa' if paise == 1 else 'Paise'}")
    return " and ".join(parts) + " Only"
