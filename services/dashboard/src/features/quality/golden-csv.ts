import { csvLines } from "@/features/insights/download";
import { parseCsv } from "@/lib/csv";

/**
 * Golden-set CSV (D9), in the engine's import format (evaluation/golden.py `import_from_csv`):
 * columns `query,expected_answer,expected_chunk_ids`, chunk ids comma-separated inside one cell.
 * Written without a byte-order mark so the file imports back unchanged.
 */
export const GOLDEN_COLUMNS = ["query", "expected_answer", "expected_chunk_ids"] as const;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (s: string) => UUID.test(s);

/** Comma / whitespace separated passage ids → trimmed list (empty entries dropped). */
export function splitChunkIds(raw: string): string[] {
	return raw
		.split(/[\s,]+/)
		.map((s) => s.trim())
		.filter(Boolean);
}

export const GOLDEN_TEMPLATE = csvLines([
	[...GOLDEN_COLUMNS],
	[
		"Do I need to fast for a lipid profile?",
		"Yes. Please fast for 10 to 12 hours before a lipid profile; water is fine.",
		"",
	],
	["लिपिड प्रोफाइल के लिए उपवास ज़रूरी है?", "हाँ, लिपिड प्रोफाइल से पहले 10–12 घंटे उपवास करें; पानी पी सकते हैं।", ""],
	[
		"Is home collection available in Baner on Sunday?",
		"Yes, home collection runs in Baner on Sundays from 7 am to 11 am. Book by Saturday evening.",
		"",
	],
]);

export interface GoldenRow {
	query: string;
	expected_answer: string;
	expected_chunk_ids?: string[] | null;
}

export function goldenToCsv(items: GoldenRow[]): string {
	return csvLines([
		[...GOLDEN_COLUMNS],
		...items.map((p) => [p.query, p.expected_answer, (p.expected_chunk_ids ?? []).join(",")]),
	]);
}

export type GoldenPreview =
	| { ok: true; ready: number; skipped: number }
	| { ok: false; error: "missing_columns" }
	| { ok: false; error: "bad_chunk"; row: number; id: string };

/**
 * What the engine will do with this file: rows without a question or an answer are skipped
 * (as the engine does); a chunk id that isn't a UUID is refused here, because the engine would
 * fail the whole import on it.
 */
export function previewGoldenCsv(text: string): GoldenPreview {
	const [header = [], ...rows] = parseCsv(text);
	const qi = header.indexOf("query");
	const ai = header.indexOf("expected_answer");
	const ci = header.indexOf("expected_chunk_ids");
	if (qi < 0 || ai < 0) return { ok: false, error: "missing_columns" };
	let ready = 0;
	let skipped = 0;
	for (const [i, r] of rows.entries()) {
		const q = (r[qi] ?? "").trim();
		const a = (r[ai] ?? "").trim();
		if (!q || !a) {
			skipped++;
			continue;
		}
		if (ci >= 0) {
			const bad = splitChunkIds(r[ci] ?? "").find((id) => !isUuid(id));
			if (bad) return { ok: false, error: "bad_chunk", row: i + 2, id: bad };
		}
		ready++;
	}
	return { ok: true, ready, skipped };
}
