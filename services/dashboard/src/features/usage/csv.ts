import { type Currency, toDisplayAmount } from "@/lib/format/currency";

/**
 * Usage CSV (D2): UTF-8 with a BOM so Excel opens Hindi and Marathi text correctly; columns
 * `key,label,requests,prompt_tokens,completion_tokens,cost_usd,cost_<currency>,unpriced_requests`;
 * CRLF line ends (RFC 4180). Values that contain a comma, quote or line break are quoted, quotes
 * doubled. A row whose price is unknown has empty cost cells (never a zero) and its
 * `unpriced_requests` says how many requests are missing from a partial cost.
 */
export interface CsvRow {
	key: string | null;
	label: string;
	requests: number;
	prompt_tokens: number;
	completion_tokens: number;
	cost_usd: number;
	/** False when some requests have no price; absent means known. */
	cost_known?: boolean;
	unpriced_requests?: number;
}

export const BOM = "﻿";

export function csvCell(value: string | number | null): string {
	if (value == null) return "";
	const s = String(value);
	// A leading = + - @ would run as a formula in a spreadsheet; prefix it (OWASP CSV injection).
	const safe = /^[=+\-@\t\r]/.test(s) && typeof value === "string" ? `'${s}` : s;
	return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export function usageCsv(rows: CsvRow[], totals: CsvRow, currency: Currency): string {
	const code = currency === "USD" ? "usd" : "inr";
	const header = [
		"key",
		"label",
		"requests",
		"prompt_tokens",
		"completion_tokens",
		"cost_usd",
		`cost_${code}`,
		"unpriced_requests",
	];
	const line = (r: CsvRow) => {
		const unknown = r.cost_known === false && r.cost_usd === 0;
		return [
			csvCell(r.key),
			csvCell(r.label),
			csvCell(r.requests),
			csvCell(r.prompt_tokens),
			csvCell(r.completion_tokens),
			unknown ? "" : csvCell(round(r.cost_usd, 6)),
			unknown ? "" : csvCell(round(toDisplayAmount(r.cost_usd, currency), currency === "USD" ? 6 : 2)),
			csvCell(r.unpriced_requests ?? 0),
		].join(",");
	};
	return `${BOM}${[header.join(","), ...rows.map(line), line(totals)].join("\r\n")}\r\n`;
}

function round(n: number, digits: number): number {
	const f = 10 ** digits;
	return Math.round(n * f) / f;
}

export function usageCsvName(start: string, end: string, group: string): string {
	return `usage-${start}-${end}-${group}.csv`;
}

/** Saves text as a file through a temporary object URL (no server round trip). */
export function downloadText(text: string, fileName: string, type = "text/csv;charset=utf-8"): void {
	const url = URL.createObjectURL(new Blob([text], { type }));
	const a = document.createElement("a");
	a.href = url;
	a.download = fileName;
	document.body.append(a);
	a.click();
	a.remove();
	setTimeout(() => URL.revokeObjectURL(url), 0);
}
