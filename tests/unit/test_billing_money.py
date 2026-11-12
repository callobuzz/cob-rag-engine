"""The integer money helper: formatting, parsing, arithmetic, micro conservation."""

import json
import random
from pathlib import Path
from typing import Any

import pytest

from callobuzz_rag.billing.money import (
    CURRENCIES,
    MICRO_PER_MINOR,
    PPM,
    CurrencyMismatchError,
    Money,
    MoneyError,
    fx_ppm,
    pct_of,
    split_micro,
    usd_micro_to_micro,
    with_markup,
)

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "tests/fixtures/money_vectors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("v", VECTORS["format"], ids=lambda v: f"{v['currency']}{v['minor']}")
def test_format(v: dict[str, Any]) -> None:
    assert Money(v["minor"], v["currency"]).format() == v["text"]


@pytest.mark.parametrize("v", VECTORS["parse"], ids=lambda v: v["text"] or "empty")
def test_parse(v: dict[str, Any]) -> None:
    if v["minor"] is None:
        with pytest.raises(MoneyError):
            Money.parse(v["text"], v["currency"])
    else:
        assert Money.parse(v["text"], v["currency"]).minor == v["minor"]


@pytest.mark.parametrize("seed", range(200))
def test_parse_format_round_trip_and_arithmetic(seed: int) -> None:
    rng = random.Random(seed)
    a, b = (Money(rng.randint(-(10**12), 10**12), rng.choice(CURRENCIES)) for _ in range(2))
    assert Money.parse(a.format(), a.currency) == a
    assert Money.parse(a.amount_str(), a.currency) == a
    if a.currency == b.currency:
        assert (a + b) - b == a
        negated = -a
        assert -negated == a
    else:
        with pytest.raises(CurrencyMismatchError):
            a + b


def test_money_refuses_floats_and_bools() -> None:
    for bad in (1.0, True, "1"):
        with pytest.raises(MoneyError):
            Money(bad, "INR")  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        Money(1, "EUR")  # type: ignore[arg-type]


def test_amount_str_and_zero() -> None:
    assert Money(123450, "INR").amount_str() == "1234.50"
    assert Money(-300, "USD").amount_str() == "-3.00"
    assert Money.zero("USD") == Money(0, "USD")


@pytest.mark.parametrize("seed", range(200))
def test_split_micro_conserves(seed: int) -> None:
    micro = random.Random(seed).randint(0, 10**15)
    minor, rest = split_micro(micro)
    assert minor * MICRO_PER_MINOR + rest == micro
    assert 0 <= rest < MICRO_PER_MINOR


def test_split_micro_refuses_negative() -> None:
    with pytest.raises(MoneyError):
        split_micro(-1)


def test_markup_and_fx() -> None:
    assert with_markup(1_000_000, 0) == 1_000_000
    assert with_markup(1_000_000, 2500) == 1_250_000
    assert fx_ppm(84.0, "INR") == 84_000_000
    assert fx_ppm(84.0, "USD") == PPM
    assert usd_micro_to_micro(1_000, fx_ppm(84.0, "INR")) == 84_000  # $0.001 -> Rs 0.084
    for x in range(0, 10_000, 37):  # monotone, never negative
        assert 0 <= with_markup(x, 1000) <= with_markup(x + 1, 1000)


def test_pct_of() -> None:
    assert pct_of(1999, 20) == 399
    assert pct_of(5, 0) == 0
    with pytest.raises(MoneyError):
        pct_of(5, 101)
