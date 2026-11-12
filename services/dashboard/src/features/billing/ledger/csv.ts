import { BOM, csvCell } from "@/features/usage/csv";
import type { LedgerEntry } from "@/lib/api/billing-types";
import { minorToInput } from "@/lib/format/money";
import { ledgerStrings as s } from "@/lib/strings/billing";

/** The transactions on screen as a CSV: UTF-8 with a BOM, CRLF, amounts as plain decimals. The
 * amount cells are written bare: they come only from `minorToInput` (digits, "-" and "."), and the
 * formula guard in `csvCell` would turn every negative one into text. */
export function ledgerCsv(items: LedgerEntry[]): string {
	const header = ["date", "type", "description", "amount", "balance_after", "currency"];
	const lines = items.map((e) =>
		[
			csvCell(e.created_at.slice(0, 10)),
			csvCell(s.types[e.entry_type]),
			csvCell(e.memo_public ?? ""),
			minorToInput(e.amount_minor),
			minorToInput(e.balance_after_minor),
			csvCell(e.currency),
		].join(","),
	);
	return `${BOM}${[header.join(","), ...lines].join("\r\n")}\r\n`;
}
