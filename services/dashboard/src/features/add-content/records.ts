import type { components } from "@/lib/api/schema";
import { csvToObjects } from "@/lib/csv";
import { contentStrings } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";

/**
 * Records import (D4): source rows → mapping → `RecordInput[]` with per-row validation, then
 * batches of the server's `max_records_per_request`. Row numbers are 1-based data rows (a CSV's
 * header line is not counted).
 */
export type RecordInput = components["schemas"]["RecordInput"];
export type SourceRow = Record<string, unknown>;

const t = contentStrings.records;

/** Server bounds from RecordInput in docs/openapi.json. */
export const RECORD_ID_MAX = 500;
export const RECORD_TITLE_MAX = 1000;
export const RECORD_URL_MAX = 2000;

export interface Mapping {
	id: string;
	text: string;
	title: string;
	url: string;
	metadata: string[];
}

export interface RowError {
	/** 1-based data row. */
	row: number;
	message: string;
}

export interface Source {
	columns: string[];
	rows: SourceRow[];
}

export type ParseResult = { ok: true; source: Source } | { ok: false; error: string };

/** A JSON array of objects. Columns = every key, in first-seen order. */
export function parseJsonRecords(text: string): ParseResult {
	let data: unknown;
	try {
		data = JSON.parse(text);
	} catch (e) {
		return { ok: false, error: fill(t.parseErrorJson, { error: e instanceof Error ? e.message : String(e) }) };
	}
	if (!Array.isArray(data) || data.some((r) => !r || typeof r !== "object" || Array.isArray(r))) {
		return { ok: false, error: t.parseErrorShape };
	}
	if (data.length === 0) return { ok: false, error: t.parseEmpty };
	const columns: string[] = [];
	const seen = new Set<string>();
	for (const r of data as SourceRow[])
		for (const k of Object.keys(r))
			if (!seen.has(k)) {
				seen.add(k);
				columns.push(k);
			}
	return { ok: true, source: { columns, rows: data as SourceRow[] } };
}

export function parseCsvRecords(text: string): ParseResult {
	const { columns, rows } = csvToObjects(text);
	if (rows.length === 0) return { ok: false, error: t.parseEmpty };
	return { ok: true, source: { columns, rows } };
}

/** First column whose name matches one of the candidates (case-insensitive), else "". */
function guess(columns: string[], candidates: RegExp[]): string {
	for (const re of candidates) {
		const hit = columns.find((c) => re.test(c.trim()));
		if (hit) return hit;
	}
	return "";
}

/** A starting mapping from common column names (the user confirms it). */
export function guessMapping(columns: string[]): Mapping {
	const id = guess(columns, [/^id$/i, /^external_?id$/i, /_id$/i, /^(sku|code|slug|key)$/i]);
	const text = guess(columns, [/^text$/i, /^answer$/i, /^(content|body|description|details)$/i]);
	const title = guess(columns, [/^title$/i, /^question$/i, /^(name|heading|subject)$/i]);
	const url = guess(columns, [/^url$/i, /^(link|href|source_?url)$/i]);
	const used = new Set([id, text, title, url].filter(Boolean));
	return { id, text, title, url, metadata: columns.filter((c) => !used.has(c)) };
}

/** Text form of a cell for ids, text and metadata: strings as-is, other values as JSON. */
export function stringify(v: unknown): string {
	if (v == null) return "";
	if (typeof v === "string") return v;
	if (typeof v === "number" || typeof v === "boolean" || typeof v === "bigint") return String(v);
	return JSON.stringify(v);
}

export interface BuildResult {
	records: RecordInput[];
	/** The 1-based source row of each record (for server errors). */
	rows: number[];
	errors: RowError[];
}

/** Map every row; rows with problems are listed and left out of `records`. */
export function buildRecords(rows: SourceRow[], mapping: Mapping, limits: { maxRecordChars: number }): BuildResult {
	const records: RecordInput[] = [];
	const sourceRows: number[] = [];
	const errors: RowError[] = [];
	const firstRowOfId = new Map<string, number>();
	rows.forEach((r, i) => {
		const row = i + 1;
		const problems: string[] = [];
		const id = stringify(r[mapping.id]).trim();
		const text = stringify(r[mapping.text]).trim();
		const title = mapping.title ? stringify(r[mapping.title]).trim() : "";
		const url = mapping.url ? stringify(r[mapping.url]).trim() : "";
		if (!id) problems.push(t.errMissingId);
		else if (id.length > RECORD_ID_MAX) problems.push(fill(t.errIdTooLong, { max: RECORD_ID_MAX }));
		else if (firstRowOfId.has(id)) problems.push(fill(t.errDuplicateId, { id, first: firstRowOfId.get(id) ?? 0 }));
		else firstRowOfId.set(id, row);
		if (!text) problems.push(t.errEmptyText);
		else if (text.length > limits.maxRecordChars)
			problems.push(fill(t.errTextTooLong, { n: text.length, max: limits.maxRecordChars }));
		if (title.length > RECORD_TITLE_MAX) problems.push(fill(t.errTitleTooLong, { max: RECORD_TITLE_MAX }));
		if (url.length > RECORD_URL_MAX) problems.push(fill(t.errUrlTooLong, { max: RECORD_URL_MAX }));
		if (problems.length) {
			for (const message of problems) errors.push({ row, message });
			return;
		}
		const metadata: Record<string, string> = {};
		for (const c of mapping.metadata) {
			const v = stringify(r[c]);
			if (v !== "") metadata[c] = v;
		}
		sourceRows.push(row);
		records.push({
			id,
			text,
			...(title ? { title } : {}),
			...(url ? { url } : {}),
			metadata,
		});
	});
	return { records, rows: sourceRows, errors };
}

/** Rows of `items` in groups of `size` (the last may be shorter). */
export function batches<T>(items: T[], size: number): T[][] {
	const n = Math.max(1, Math.floor(size));
	const out: T[][] = [];
	for (let i = 0; i < items.length; i += n) out.push(items.slice(i, i + n));
	return out;
}
