"""P2 T7: the GST-inclusive total, and the vectors the dashboard's TypeScript twin must match."""

import json
from fractions import Fraction
from math import floor
from pathlib import Path

import pytest

from callobuzz_rag.billing.invoicing.display import inclusive_total

VECTORS = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "gst_vectors.json").read_text("utf-8")
)


def test_the_examples_of_the_spec() -> None:
    assert inclusive_total(99_900, registered=True, prices_include_gst=False) == 117_882
    assert inclusive_total(99_900, registered=False, prices_include_gst=False) == 99_900
    assert inclusive_total(99_900, registered=True, prices_include_gst=True) == 99_900


def test_half_a_paisa_rounds_up() -> None:
    # 50 paise x 1.18 = 59 exactly; 5 paise x 1.18 = 5.9 -> 6; 25 paise = 29.5 -> 30
    assert inclusive_total(50, registered=True, prices_include_gst=False) == 59
    assert inclusive_total(5, registered=True, prices_include_gst=False) == 6
    assert inclusive_total(25, registered=True, prices_include_gst=False) == 30


def test_a_negative_price_is_refused() -> None:
    with pytest.raises(ValueError):
        inclusive_total(-1, registered=True, prices_include_gst=False)


def test_there_are_200_vectors_and_the_python_code_matches_an_independent_calculation() -> None:
    assert len(VECTORS) == 200
    for v in VECTORS:
        got = inclusive_total(
            v["price_minor"], registered=v["registered"], prices_include_gst=v["prices_include_gst"]
        )
        assert got == v["expected"], v
        if v["registered"] and not v["prices_include_gst"]:
            exact = Fraction(v["price_minor"] * 118, 100)
            assert got == floor(exact + Fraction(1, 2)), v
