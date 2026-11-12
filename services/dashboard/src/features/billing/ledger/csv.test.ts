import { describe, expect, it } from "vitest";
import type { LedgerEntry } from "@/lib/api/billing-types";
import { ledgerCsv } from "./csv";

function entry(over: Partial<LedgerEntry>): LedgerEntry {
	return {
		id: "e1",
		seq: 1,
		entry_type: "usage_charge",
		amount_minor: -200,
		currency: "INR",
		balance_after_minor: -50,
		memo: null,
		memo_public: "=Usage",
		reference_type: null,
		reference_id: null,
		idempotency_key: "usage:1",
		period: "2026-10-01",
		actor_label: "system",
		created_at: "2026-10-04T10:00:00Z",
		...over,
	} as LedgerEntry;
}

describe("ledgerCsv", () => {
	it("keeps negative amounts and balances numeric, not text", () => {
		const row = ledgerCsv([entry({})]).split("\r\n")[1] ?? "";
		const cells = row.split(",");
		expect(cells[3]).toBe("-2.00");
		expect(cells[4]).toBe("-0.50");
	});

	it("still guards the description against formulas", () => {
		const row = ledgerCsv([entry({})]).split("\r\n")[1] ?? "";
		expect(row.split(",")[2]).toBe("'=Usage");
	});
});
