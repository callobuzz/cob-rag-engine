import { costStrings as t } from "@/lib/strings/costs";
import { fill } from "@/lib/strings/en";
import { type Currency, formatMoney, getCurrency, toDisplayAmount } from "./currency";

/**
 * A real cost for display. The engine reports `cost_usd` and `cost_known`:
 * - no figure, or a request nobody could price, is "price unknown", never ₹0.00;
 * - a total where only part is priced reads "≥ ₹x" (a floor);
 * - a known cost too small for the currency's decimals reads "< ₹0.01", never a flat zero.
 * A genuine zero (a cached answer, a free model) still shows as ₹0.00.
 */
export function formatCost(
	usd: number | null | undefined,
	known: boolean | null | undefined = true,
	currency: Currency = getCurrency(),
	opts: { exact?: boolean } = {},
): string {
	if (usd == null || Number.isNaN(usd)) return t.unknown;
	if (known === false) return usd > 0 ? fill(t.atLeast, { amount: money(usd, currency, opts) }) : t.unknown;
	return money(usd, currency, opts);
}

function money(usd: number, currency: Currency, opts: { exact?: boolean }): string {
	if (usd > 0) {
		if (currency === "INR" && toDisplayAmount(usd, "INR") < 0.005) {
			return fill(t.tiny, { amount: formatMoney(0.01 / toDisplayAmount(1, "INR"), "INR", { exact: true }) });
		}
		if (currency === "USD" && usd < 0.00005) return fill(t.tiny, { amount: "$0.0001" });
	}
	return formatMoney(usd, currency, opts);
}

/** "₹0.07 ($0.0008)": the chosen currency first, the other beside it (playground, per answer). */
export function formatCostBoth(
	usd: number | null | undefined,
	known: boolean | null | undefined = true,
	primary: Currency = getCurrency(),
): string {
	if (usd == null || known === false) return formatCost(usd, known, primary);
	const other: Currency = primary === "INR" ? "USD" : "INR";
	const exact = { exact: true };
	return fill(t.both, {
		primary: formatCost(usd, true, primary, exact),
		secondary: formatCost(usd, true, other, exact),
	});
}
