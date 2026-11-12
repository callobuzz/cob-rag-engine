/**
 * The GST-inclusive total shown next to a price (P2): the price plus 18 % GST when the seller is
 * registered and lists prices without tax; the price itself when the seller has no GST number or
 * already lists tax-inclusive prices. Integer minor units (paise), halves round up, never a float
 * in the result. Mirrors `inclusive_total` in `billing/invoicing/display.py`; both read
 * `tests/fixtures/gst_vectors.json`.
 */
const TOTAL_PERCENT = 118;

export function inclusiveTotal(priceMinor: number, registered: boolean, pricesIncludeGst: boolean): number {
	if (!registered || pricesIncludeGst) return priceMinor;
	const scaled = priceMinor * TOTAL_PERCENT; // below 2^53 for any price this engine accepts
	return Math.floor((2 * scaled + 100) / 200);
}
