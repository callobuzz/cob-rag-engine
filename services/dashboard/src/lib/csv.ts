/**
 * In-house RFC 4180 CSV parser (D4 decision; no runtime dependency). Handles quoted fields,
 * escaped quotes (`""`), commas and line breaks inside quotes, CRLF / LF / CR line endings and a
 * UTF-8 byte-order mark. Lines that are completely empty are skipped.
 */

/** Rows of raw cells, exactly as written (no header handling). */
export function parseCsv(input: string): string[][] {
	const text = input.charCodeAt(0) === 0xfeff ? input.slice(1) : input;
	const rows: string[][] = [];
	let row: string[] = [];
	let cell = "";
	let quoted = false;
	/** True once the current row has any content (a cell, a separator or a quote). */
	let started = false;

	const endCell = () => {
		row.push(cell);
		cell = "";
	};
	const endRow = () => {
		if (started) {
			endCell();
			rows.push(row);
		}
		row = [];
		cell = "";
		started = false;
	};

	for (let i = 0; i < text.length; i++) {
		const ch = text[i];
		if (quoted) {
			if (ch === '"') {
				if (text[i + 1] === '"') {
					cell += '"';
					i++;
				} else {
					quoted = false;
				}
			} else {
				cell += ch;
			}
			continue;
		}
		if (ch === '"') {
			quoted = true;
			started = true;
		} else if (ch === ",") {
			started = true;
			endCell();
		} else if (ch === "\r") {
			if (text[i + 1] === "\n") i++;
			endRow();
		} else if (ch === "\n") {
			endRow();
		} else {
			started = true;
			cell += ch;
		}
	}
	endRow();
	return rows;
}

export interface CsvTable {
	columns: string[];
	rows: Record<string, string>[];
}

/**
 * First row = column names. Blank names become `column_N`, repeated names get `_2`, `_3`…;
 * short rows are padded with "", and cells beyond the header get their own `column_N` column.
 */
export function csvToObjects(input: string): CsvTable {
	const [header = [], ...body] = parseCsv(input);
	const columns: string[] = [];
	const seen = new Map<string, number>();
	const name = (raw: string, i: number) => {
		const base = raw.trim() || `column_${i + 1}`;
		const count = (seen.get(base) ?? 0) + 1;
		seen.set(base, count);
		return count === 1 ? base : `${base}_${count}`;
	};
	header.forEach((h, i) => {
		columns.push(name(h, i));
	});
	const width = Math.max(columns.length, ...body.map((r) => r.length));
	for (let i = columns.length; i < width; i++) columns.push(name("", i));
	const rows = body.map((cells) => {
		const out: Record<string, string> = {};
		columns.forEach((c, i) => {
			out[c] = cells[i] ?? "";
		});
		return out;
	});
	return { columns, rows };
}
