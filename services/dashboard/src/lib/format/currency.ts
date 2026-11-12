import { useSyncExternalStore } from "react";
import { readPref, writePref } from "@/lib/auth/storage";
import { getConfig } from "@/lib/config";

/**
 * Display currency (README "Demo data, locale and currency"): ₹ by default, toggle ₹ / $ in
 * Settings, stored in localStorage["cob.currency"]. The engine reports `cost_usd` (what a request
 * really cost, in dollars); conversion is display-only, at the engine's own rate (`usd_inr_rate`,
 * RAG_USD_INR_RATE, a fixed number: no live exchange-rate calls) once the header has fetched it,
 * else RAG_CONFIG.usdToInr.
 */
export type Currency = "INR" | "USD";
const KEY = "cob.currency";
const listeners = new Set<() => void>();

export function getCurrency(): Currency {
	return readPref(KEY) === "USD" ? "USD" : "INR";
}

export function setCurrency(c: Currency): void {
	writePref(KEY, c);
	for (const l of listeners) l();
}

let engineRate: number | null = null;
let rateVersion = 0;

/** The engine's rupees-per-dollar (`usd_inr_rate` on usage responses). Re-renders money on change. */
export function setEngineRate(rate: number | null | undefined): void {
	const next = typeof rate === "number" && Number.isFinite(rate) && rate > 0 ? rate : null;
	if (next === engineRate) return;
	engineRate = next;
	rateVersion++;
	for (const l of listeners) l();
}

/** The rate the screens convert at: the engine's, else the page's configured one. */
export function getUsdInrRate(): number {
	return engineRate ?? getConfig().usdToInr;
}

export function useCurrency(): Currency {
	// Subscribing to the rate version re-renders every money figure when the engine's rate arrives.
	useSyncExternalStore(
		(l) => {
			listeners.add(l);
			return () => listeners.delete(l);
		},
		() => rateVersion,
		() => rateVersion,
	);
	return useSyncExternalStore(
		(l) => {
			listeners.add(l);
			return () => listeners.delete(l);
		},
		getCurrency,
		getCurrency,
	);
}

/** The display amount (₹ converted at the engine's rate, or $) as a number, for CSV. */
export function toDisplayAmount(usd: number, currency: Currency = getCurrency()): number {
	return currency === "USD" ? usd : usd * getUsdInrRate();
}

/**
 * Money in the display currency, en-IN grouping. By default small ₹ amounts keep paise and larger
 * ones are rounded to the rupee; `exact` (invoice tables) always shows two decimals (₹1,23,456.78).
 */
export function formatMoney(
	usd: number | null | undefined,
	currency: Currency = getCurrency(),
	opts: { exact?: boolean } = {},
): string {
	if (usd == null || Number.isNaN(usd)) return "—";
	if (currency === "USD") {
		return new Intl.NumberFormat("en-IN", {
			style: "currency",
			currency: "USD",
			minimumFractionDigits: opts.exact ? 2 : undefined,
			maximumFractionDigits: 4,
		}).format(usd);
	}
	const inr = toDisplayAmount(usd, "INR");
	return new Intl.NumberFormat("en-IN", {
		style: "currency",
		currency: "INR",
		...(opts.exact ? { minimumFractionDigits: 2 } : {}),
		maximumFractionDigits: opts.exact || inr < 10 ? 2 : 0,
	}).format(inr);
}
