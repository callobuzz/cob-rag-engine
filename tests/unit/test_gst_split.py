import random
import re
from pathlib import Path

import pytest

from callobuzz_rag.billing.invoicing.gst import (
    half_up,
    refund_split,
    split_exclusive,
    split_inclusive,
    supply_kind,
)


def test_research_examples() -> None:
    s = split_exclusive(99_900, "intra", "27")
    assert (s.cgst_minor, s.sgst_minor, s.igst_minor, s.total_minor) == (8_991, 8_991, 0, 117_882)
    s = split_exclusive(100_000, "inter", "29")
    assert (s.cgst_minor, s.sgst_minor, s.igst_minor, s.total_minor) == (0, 0, 18_000, 118_000)
    assert split_exclusive(100_000, "export_lut", "96").total_minor == 100_000
    assert split_exclusive(100_000, "no_gst", "27").total_minor == 100_000


def test_supply_kind_table() -> None:
    def k(**kw: object) -> str:
        base: dict[str, object] = {
            "seller_registered": True,
            "seller_state": "27",
            "buyer_state": "27",
            "buyer_country": "IN",
            "lut_valid": False,
        }
        return supply_kind(**{**base, **kw})  # type: ignore[arg-type]

    assert k() == "intra"
    assert k(buyer_state="07") == "inter"
    assert k(buyer_country="AE", buyer_state=None) == "export_igst"
    assert k(buyer_country="AE", buyer_state=None, lut_valid=True) == "export_lut"
    assert k(seller_registered=False) == "no_gst"


@pytest.mark.parametrize("seed", range(300))
def test_inclusive_total_is_exact_and_tax_is_close_to_18pct(seed: int) -> None:
    gross = random.Random(seed).randint(1, 10**9)
    for kind, pos in (("intra", "27"), ("inter", "07")):
        s = split_inclusive(gross, kind, pos)  # type: ignore[arg-type]
        assert s.total_minor == gross
        assert s.cgst_minor == s.sgst_minor
        assert s.taxable_minor >= 0
        assert abs((s.cgst_minor + s.sgst_minor + s.igst_minor) * 100 - s.taxable_minor * 18) <= 118


@pytest.mark.parametrize("seed", range(300))
def test_exclusive_halves_are_equal_and_never_negative(seed: int) -> None:
    taxable = random.Random(seed).randint(0, 10**10)
    s = split_exclusive(taxable, "intra", "27")
    assert s.cgst_minor == s.sgst_minor == half_up(taxable * 900, 10_000)
    assert s.total_minor == taxable + 2 * s.cgst_minor


def test_refund_split_never_exceeds_original() -> None:
    original = split_exclusive(99_900, "intra", "27")
    part = refund_split(original, 50_000)
    assert part.kind == "intra" and part.place_of_supply == "27"
    assert part.cgst_minor <= original.cgst_minor and part.total_minor <= original.total_minor
    assert (part.cgst_minor, part.sgst_minor, part.total_minor) == (4_500, 4_500, 59_000)
    assert refund_split(original, 99_900) == original
    with pytest.raises(ValueError):
        refund_split(original, 99_901)


@pytest.mark.parametrize("seed", range(200))
def test_refund_parts_stay_within_the_original(seed: int) -> None:
    rng = random.Random(seed)
    taxable = rng.randint(1, 10**7)
    original = split_exclusive(taxable, "inter", "29")
    first = rng.randint(0, taxable)
    a, b = refund_split(original, first), refund_split(original, taxable - first)
    assert a.igst_minor <= original.igst_minor and b.igst_minor <= original.igst_minor
    assert a.igst_minor + b.igst_minor <= original.igst_minor + 1  # two roundings at most


def test_half_up() -> None:
    assert (half_up(5, 10), half_up(4, 10), half_up(15, 10)) == (1, 0, 2)
    with pytest.raises(ValueError):
        half_up(-1, 10)


def test_no_float_in_tax_code() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "callobuzz_rag" / "billing" / "invoicing"
    for name in ("gst.py", "gstin.py"):
        text = (root / name).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", text, flags=re.S)
        code = re.sub(r"#.*", "", code)
        assert "float(" not in code
        assert not re.search(r"\d\.\d", code)
        assert " / " not in code
