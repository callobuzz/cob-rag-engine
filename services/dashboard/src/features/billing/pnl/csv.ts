import { BOM, csvCell } from "@/features/usage/csv";
import type { PnlReport } from "@/lib/api/billing-types";

/** `1234.50` / `-3.00` from integer minor units: digits only, never through a float. */
export function decimal(minor: number): string {
	const abs = Math.abs(minor);
	return `${minor < 0 ? "-" : ""}${Math.trunc(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

/**
 * The P&L as a spreadsheet: one line per customer and a total, amounts as plain decimals in the
 * report's currency, AI cost marked as an estimate when some calls had no known price. UTF-8 with
 * a BOM and CRLF line ends, like the other CSV downloads.
 */
export function pnlCsv(report: PnlReport): string {
	const head = [
		"customer",
		"billed",
		"paid_in",
		"corrections",
		"ai_cost_estimate",
		"unpriced_calls",
		"margin",
		"margin_percent",
		"internal_plan",
	];
	const lines = [head.join(",")];
	for (const r of report.rows) {
		lines.push(
			[
				csvCell(r.name),
				decimal(r.billed_minor),
				decimal(r.collected_minor),
				decimal(r.adjustments_minor),
				decimal(r.ai_cost_minor),
				r.unpriced_calls ?? 0,
				decimal(r.margin_minor),
				r.margin_pct == null ? "" : r.margin_pct,
				r.exempt ? "yes" : "",
			].join(","),
		);
	}
	const t = report.totals;
	lines.push(
		[
			"Total",
			decimal(t.billed_minor),
			decimal(t.collected_minor),
			decimal(t.adjustments_minor),
			decimal(t.ai_cost_minor),
			t.unpriced_calls ?? 0,
			decimal(t.margin_minor),
			t.margin_pct == null ? "" : t.margin_pct,
			"",
		].join(","),
	);
	return `${BOM}${lines.join("\r\n")}\r\n`;
}
