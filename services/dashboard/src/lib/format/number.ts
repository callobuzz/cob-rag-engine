/** en-IN grouping (1,00,000). */
const intFmt = new Intl.NumberFormat("en-IN");
const compactFmt = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });

export function formatNumber(n: number | null | undefined): string {
	return n == null || Number.isNaN(n) ? "—" : intFmt.format(n);
}

export function formatCompact(n: number | null | undefined): string {
	return n == null || Number.isNaN(n) ? "—" : compactFmt.format(n);
}

export function formatPercent(fraction: number | null | undefined, digits = 0): string {
	if (fraction == null || Number.isNaN(fraction)) return "—";
	return `${(fraction * 100).toFixed(digits)}%`;
}
