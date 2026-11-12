/**
 * Which of a card's variants a visitor's choice shows. The same order as `choose_index` in
 * `landing/pricing.py`: the exact (currency, period), else the same currency monthly, else the
 * same currency in any period, else the first.
 */
export type Option = readonly [currency: string, period: string];

export function chooseIndex(options: readonly Option[], currency: string, period: string): number {
	for (const wanted of [
		[currency, period],
		[currency, "monthly"],
	] as const) {
		const at = options.findIndex((o) => o[0] === wanted[0] && o[1] === wanted[1]);
		if (at >= 0) return at;
	}
	const same = options.findIndex((o) => o[0] === currency);
	return same >= 0 ? same : 0;
}
