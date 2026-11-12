"""The GST-inclusive total shown next to a price (no drip pricing: the total is never a surprise).

`inclusive_total(price_minor, ...)` is the price with 18 % GST added when the seller is registered
and prices are listed without tax; the price itself when the seller has no GSTIN or already lists
tax-inclusive prices. It is a display figure: CGST + SGST and IGST are both 18 %, so the total is
the same for every buyer. The invoice itself (`gst.split_exclusive`) rounds each tax line, so for a
CGST + SGST sale it can differ from this figure by one paisa on odd amounts; the checkout quote
and the invoice are exact. The dashboard mirrors this in `lib/format/gst.ts`; both read
`tests/fixtures/gst_vectors.json`.
"""

from callobuzz_rag.billing.invoicing.gst import RATE_BP, half_up


def inclusive_total(price_minor: int, *, registered: bool, prices_include_gst: bool) -> int:
    """`price_minor` x 1.18, half up, when GST is added on top; else `price_minor`."""
    if price_minor < 0:
        raise ValueError("a price is never negative")
    if not registered or prices_include_gst:
        return price_minor
    return half_up(price_minor * (10_000 + RATE_BP), 10_000)
