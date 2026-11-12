"""The GST split of a price (18 %), pure and integer-only.

Amounts are whole minor units (paise). Nothing here uses a float: every rounding is `half_up`, an
integer division that rounds .5 up. A tax invoice and the payment that pays it must always agree,
so `total_minor` is exact: `taxable + cgst + sgst + igst`, and for an inclusive price it equals
the price that was typed.
"""

from dataclasses import dataclass
from typing import Final, Literal

RATE_BP: Final = 1800  # 18 % in basis points

Kind = Literal["intra", "inter", "export_lut", "export_igst", "no_gst"]


@dataclass(frozen=True, slots=True)
class TaxSplit:
    kind: Kind
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    total_minor: int
    place_of_supply: str

    @property
    def tax_minor(self) -> int:
        return self.cgst_minor + self.sgst_minor + self.igst_minor


def half_up(numerator: int, denominator: int) -> int:
    """`numerator // denominator` rounded to the nearest integer, halves up (numerator >= 0)."""
    if numerator < 0 or denominator <= 0:
        raise ValueError("half_up needs a non-negative numerator and a positive denominator")
    return (2 * numerator + denominator) // (2 * denominator)


def supply_kind(
    *,
    seller_registered: bool,
    seller_state: str,
    buyer_state: str | None,
    buyer_country: str,
    lut_valid: bool,
) -> Kind:
    """Which tax applies: none (seller not registered), export, or CGST+SGST / IGST by state."""
    if not seller_registered:
        return "no_gst"
    if buyer_country != "IN":
        return "export_lut" if lut_valid else "export_igst"
    return "intra" if buyer_state == seller_state else "inter"


def _make(kind: Kind, taxable: int, cgst: int, sgst: int, igst: int, place: str) -> TaxSplit:
    return TaxSplit(kind, taxable, cgst, sgst, igst, taxable + cgst + sgst + igst, place)


def split_exclusive(taxable_minor: int, kind: Kind, place_of_supply: str) -> TaxSplit:
    """Tax added on top of `taxable_minor`."""
    if taxable_minor < 0:
        raise ValueError("amount must not be negative")
    if kind == "intra":
        half = half_up(taxable_minor * (RATE_BP // 2), 10_000)
        return _make(kind, taxable_minor, half, half, 0, place_of_supply)
    if kind in ("inter", "export_igst"):
        return _make(
            kind, taxable_minor, 0, 0, half_up(taxable_minor * RATE_BP, 10_000), place_of_supply
        )
    return _make(kind, taxable_minor, 0, 0, 0, place_of_supply)


def split_inclusive(gross_minor: int, kind: Kind, place_of_supply: str) -> TaxSplit:
    """The tax inside a GST-inclusive price: `total_minor == gross_minor`, always."""
    if gross_minor < 0:
        raise ValueError("amount must not be negative")
    if kind == "intra":
        half = half_up(gross_minor * 9, 118)
        return _make(kind, gross_minor - 2 * half, half, half, 0, place_of_supply)
    if kind in ("inter", "export_igst"):
        igst = half_up(gross_minor * 18, 118)
        return _make(kind, gross_minor - igst, 0, 0, igst, place_of_supply)
    return _make(kind, gross_minor, 0, 0, 0, place_of_supply)


def refund_split(original: TaxSplit, refund_taxable_minor: int) -> TaxSplit:
    """The part of `original` that a refund of `refund_taxable_minor` (of the taxable value) takes
    back. Tax is proportional, rounded half up, never more than the original's. The caller passes
    only what is still refundable, so the whole is never exceeded."""
    if refund_taxable_minor < 0 or refund_taxable_minor > original.taxable_minor:
        raise ValueError("refund is larger than the original taxable value")
    if refund_taxable_minor == original.taxable_minor:
        return original

    def share(part: int) -> int:
        if original.taxable_minor == 0:
            return 0
        return min(part, half_up(part * refund_taxable_minor, original.taxable_minor))

    cgst = share(original.cgst_minor)
    return _make(
        original.kind,
        refund_taxable_minor,
        cgst,
        cgst if original.sgst_minor == original.cgst_minor else share(original.sgst_minor),
        share(original.igst_minor),
        original.place_of_supply,
    )
