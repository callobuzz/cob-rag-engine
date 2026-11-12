import { afterEach, describe, expect, it } from "vitest";
import { formatMoney, setCurrency, toDisplayAmount } from "@/lib/format/currency";
import { BOM, type CsvRow, csvCell, usageCsv, usageCsvName } from "./csv";
import { sourceKind, sourceLabel, totalsBySource, widgetKeyId } from "./source-labels";

afterEach(() => setCurrency("INR"));

describe("currency", () => {
	it("₹ by default with en-IN grouping; exact shows paise; $ toggle", () => {
		// usdToInr 85 (config default): 1452.43 USD → ₹1,23,456.55
		expect(formatMoney(1452.43, "INR", { exact: true })).toBe("₹1,23,456.55");
		expect(formatMoney(1452.43, "INR")).toBe("₹1,23,457");
		expect(formatMoney(0.05, "INR")).toBe("₹4.25");
		expect(formatMoney(12.5, "USD", { exact: true })).toBe("$12.50");
		expect(formatMoney(0.0012, "USD")).toBe("$0.0012");
		expect(formatMoney(null)).toBe("—");
		expect(toDisplayAmount(2, "INR")).toBe(170);
		expect(toDisplayAmount(2, "USD")).toBe(2);
	});
});

describe("source labels", () => {
	it("known sources, widget keys by name, unknown widget id, anything else", () => {
		const names = new Map([["k1", { name: "shree-labs.in", kbName: "Patient help" }]]);
		expect(sourceLabel("api")).toBe("Your apps (API)");
		expect(sourceLabel("stream")).toBe("Your apps (streaming)");
		expect(sourceLabel("conversation")).toBe("Conversations");
		expect(sourceLabel("eval")).toBe("Test runs");
		expect(sourceLabel("assistant")).toBe("Assistant editor (Improve this rule)");
		expect(sourceLabel("cob")).toBe("COB, the built-in assistant");
		expect(sourceLabel("cob_public")).toBe("COB on the sign-in page");
		expect(sourceLabel("widget:k1", names)).toBe("Widget · Patient help · shree-labs.in");
		expect(sourceLabel("widget:deadbeef-0000-4000", names)).toBe("Widget deadbeef");
		expect(sourceLabel("mcp")).toBe("mcp");
		expect(sourceKind("widget:x")).toBe("widget");
		expect(widgetKeyId("api")).toBeNull();
		expect(
			totalsBySource([
				{ key: "widget:a", requests: 2 },
				{ key: "widget:b", requests: 3 },
				{ key: "api", requests: 4 },
			]),
		).toEqual({ widget: 5, api: 4, stream: 0, conversation: 0, eval: 0, other: 0 });
	});
});

describe("usage CSV", () => {
	const rows: CsvRow[] = [
		{ key: "a", label: "Shree Labs, Pune", requests: 10, prompt_tokens: 100, completion_tokens: 20, cost_usd: 0.5 },
		{ key: "b", label: 'The "Staff" SOPs', requests: 5, prompt_tokens: 50, completion_tokens: 10, cost_usd: 0.25 },
		{ key: "c", label: "रुग्ण मदत", requests: 1, prompt_tokens: 1, completion_tokens: 1, cost_usd: 0 },
		{ key: "d", label: "=HYPERLINK(1)", requests: 0, prompt_tokens: 0, completion_tokens: 0, cost_usd: 0 },
	];
	const totals: CsvRow = {
		key: null,
		label: "Total",
		requests: 16,
		prompt_tokens: 151,
		completion_tokens: 31,
		cost_usd: 0.75,
	};

	it("escapes commas, quotes and formulas, keeps Hindi/Marathi, starts with a BOM", () => {
		const csv = usageCsv(rows, totals, "INR");
		expect(csv.startsWith(BOM)).toBe(true);
		const lines = csv.slice(1).trimEnd().split("\r\n");
		expect(lines[0]).toBe("key,label,requests,prompt_tokens,completion_tokens,cost_usd,cost_inr,unpriced_requests");
		expect(lines[1]).toBe('a,"Shree Labs, Pune",10,100,20,0.5,42.5,0');
		expect(lines[2]).toBe('b,"The ""Staff"" SOPs",5,50,10,0.25,21.25,0');
		expect(lines[3]).toBe("c,रुग्ण मदत,1,1,1,0,0,0");
		expect(lines[4]).toBe("d,'=HYPERLINK(1),0,0,0,0,0,0");
		expect(lines[5]).toBe(",Total,16,151,31,0.75,63.75,0");
		expect(usageCsv(rows, totals, "USD").split("\r\n")[0]).toContain("cost_usd,cost_usd");
		expect(csvCell("a\nb")).toBe('"a\nb"');
	});

	it("a row nobody could price has empty cost cells, never a zero; a partial one says how many are missing", () => {
		const unknown: CsvRow = {
			...(rows[0] as CsvRow),
			key: "u",
			label: "Unpriced",
			cost_usd: 0,
			cost_known: false,
			unpriced_requests: 4,
		};
		const partial: CsvRow = {
			...(rows[0] as CsvRow),
			key: "p",
			label: "Partial",
			cost_usd: 0.5,
			cost_known: false,
			unpriced_requests: 2,
		};
		const lines = usageCsv([unknown, partial], totals, "INR").slice(1).trimEnd().split("\r\n");
		expect(lines[1]).toBe("u,Unpriced,10,100,20,,,4");
		expect(lines[2]).toBe("p,Partial,10,100,20,0.5,42.5,2");
		expect(usageCsvName("2026-09-01", "2026-09-30", "corpus")).toBe("usage-2026-09-01-2026-09-30-corpus.csv");
	});
});
