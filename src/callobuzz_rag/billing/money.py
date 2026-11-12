"""Integer money: minor units (paise / cents) with a currency, and the micro-unit helpers.

Nothing here touches a float except `fx_ppm`, which rounds the operator's fixed exchange
rate once into integer parts per million. A ledger amount is `Money.minor`; a usage charge
is finer (account-currency micro units, 1e-6 of the major unit) so an answer that costs a
fraction of a paisa is neither rounded to zero nor to a whole paisa: `split_micro` turns a
sum of them into minor units plus a remainder the posting carries forward.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal, get_args

from callobuzz_rag.core.exceptions import RAGKernelError

Currency = Literal["INR", "USD"]
CURRENCIES: Final[tuple[Currency, ...]] = get_args(Currency)
MINOR_PER_MAJOR: Final = 100  # paise per rupee, cents per dollar
MICRO_PER_MINOR: Final = 10_000  # account-currency micro units per minor unit
PPM: Final = 1_000_000

SYMBOLS: Final[dict[str, str]] = {"INR": "₹", "USD": "$"}
MINUS: Final = "−"  # noqa: RUF001
_MAX_INTEGER_DIGITS: Final = 15
_PARSE_RE: Final = re.compile(r"^(?P<neg>-)?(?P<int>\d+)(?:\.(?P<frac>\d{1,2}))?$")


class MoneyError(RAGKernelError):
    """A money value that is malformed, or an operation that makes no sense."""


class CurrencyMismatchError(MoneyError):
    """Two amounts in different currencies were combined."""


def _group(digits: str, currency: str) -> str:
    """Thousands separators: Indian (3 then 2-2-2) for INR, western (3-3-3) otherwise."""
    if len(digits) <= 3:
        return digits
    parts: list[str] = []
    if currency == "INR":
        head, tail = digits[:-3], digits[-3:]
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        return ",".join([*parts, tail])
    while len(digits) > 3:
        parts.insert(0, digits[-3:])
        digits = digits[:-3]
    parts.insert(0, digits)
    return ",".join(parts)


@dataclass(frozen=True, slots=True)
class Money:
    minor: int
    currency: Currency

    def __post_init__(self) -> None:
        if type(self.minor) is not int:  # bool and float are refused on purpose
            raise MoneyError("Money amounts are whole minor units (an int).")
        if self.currency not in CURRENCIES:
            raise MoneyError(f"Unsupported currency: {self.currency!r}.")

    def _check(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise MoneyError("Money can only be combined with Money.")
        if other.currency != self.currency:
            raise CurrencyMismatchError(
                f"Cannot combine {self.currency} with {other.currency} without a conversion."
            )

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.minor - other.minor, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.minor, self.currency)

    @classmethod
    def zero(cls, currency: Currency) -> "Money":
        return cls(0, currency)

    @classmethod
    def parse(cls, text: str, currency: Currency) -> "Money":
        """`"1,234.5"` -> 123450. Strips the symbol, spaces and grouping; refuses more than two
        decimals, exponents, NaN/inf, empty input and more than 15 integer digits. Integer math
        on the string only."""
        if not isinstance(text, str):
            raise MoneyError("Enter an amount as text.")
        cleaned = text.strip().replace(MINUS, "-")
        for symbol in SYMBOLS.values():
            cleaned = cleaned.replace(symbol, "")
        cleaned = cleaned.replace(",", "").replace(" ", "")
        match = _PARSE_RE.match(cleaned)
        if match is None:
            raise MoneyError("Enter an amount like 1,234.50 (at most two decimals).")
        whole = match["int"]
        if len(whole) > _MAX_INTEGER_DIGITS:
            raise MoneyError("That amount is too large.")
        frac = (match["frac"] or "").ljust(2, "0")
        minor = int(whole) * MINOR_PER_MAJOR + int(frac)
        return cls(-minor if match["neg"] else minor, currency)

    def amount_str(self) -> str:
        """`"1234.50"` / `"-3.00"`: no symbol, no grouping."""
        whole, frac = divmod(abs(self.minor), MINOR_PER_MAJOR)
        sign = "-" if self.minor < 0 else ""
        return f"{sign}{whole}.{frac:02d}"

    def format(self) -> str:
        """`₹1,23,456.78`, `$123,456.78`, `-₹12.00` with a real minus sign (U+2212)."""
        whole, frac = divmod(abs(self.minor), MINOR_PER_MAJOR)
        sign = MINUS if self.minor < 0 else ""
        return f"{sign}{SYMBOLS[self.currency]}{_group(str(whole), self.currency)}.{frac:02d}"


def fx_ppm(usd_inr_rate: float, currency: Currency) -> int:
    """USD -> 1_000_000; INR -> the fixed rate in parts per million."""
    if currency == "USD":
        return PPM
    return round(usd_inr_rate * PPM)


def usd_micro_to_micro(usd_micro: int, fx: int) -> int:
    """USD micro units to account-currency micro units at `fx` (ppm), rounded half up."""
    if usd_micro < 0:
        raise MoneyError("A cost is never negative.")
    return (usd_micro * fx + PPM // 2) // PPM


def with_markup(micro: int, markup_bp: int) -> int:
    """`micro` plus a markup in basis points (2500 = 25 %), rounded half up."""
    if micro < 0 or markup_bp < 0:
        raise MoneyError("Amount and markup are never negative.")
    return (micro * (10_000 + markup_bp) + 5_000) // 10_000


def minor_to_micro(minor: int) -> int:
    return minor * MICRO_PER_MINOR


def split_micro(micro: int) -> tuple[int, int]:
    """`(minor, rest)` with `minor * MICRO_PER_MINOR + rest == micro` and `0 <= rest < 10_000`."""
    if micro < 0:
        raise MoneyError("Only non-negative micro amounts can be split.")
    return divmod(micro, MICRO_PER_MINOR)


def pct_of(minor: int, pct: int) -> int:
    """`floor(minor * pct / 100)` for `0 <= pct <= 100`."""
    if not 0 <= pct <= 100:
        raise MoneyError("A percentage is between 0 and 100.")
    return minor * pct // 100
