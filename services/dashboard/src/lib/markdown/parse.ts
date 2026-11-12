import { parseInline } from "./inline";
import type { Item, MdAlign, MdBlock, MdInline, ParseOptions, RawBlock } from "./types";

const MAX_QUOTE_DEPTH = 3;

/** `lines[i]`, or "" past the end (`noUncheckedIndexedAccess`). */
export const ln = (lines: readonly string[], i: number): string => lines[i] ?? "";
const grp = (m: RegExpExecArray, k: number): string => m[k] ?? "";

// Bidi overrides/isolates (trojan-source style spoofing) and control characters other than \n and \t.
// biome-ignore lint/suspicious/noControlCharactersInRegex: stripping control characters is the point
const STRIP = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g;

/** Normalises line endings and removes characters that are never text. */
export function clean(text: string): string {
	return String(text).replace(/\r\n?/g, "\n").replace(STRIP, "");
}

const FENCE = /^([ \t]*)(`{3,}|~{3,})(.*)$/;
const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/;
const HR = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const QUOTE = /^ {0,3}> ?/;
const ITEM = /^([ \t]*)([-*+•]|\d{1,9}[.)])[ \t]+(\S.*)$/;
const DELIM = /^\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?$/;

export function indentOf(line: string): number {
	let n = 0;
	for (const c of line) {
		if (c === " ") n++;
		else if (c === "\t") n += 4;
		else break;
	}
	return n;
}

export function fenceOpen(line: string): { indent: number; ch: string; len: number; lang: string } | null {
	const m = FENCE.exec(line);
	if (!m) return null;
	const info = grp(m, 3).trim();
	if (grp(m, 2).charAt(0) === "`" && info.includes("`")) return null;
	const word = info.split(/\s+/)[0] ?? "";
	return {
		indent: grp(m, 1).length,
		ch: grp(m, 2).charAt(0),
		len: grp(m, 2).length,
		lang: /^[A-Za-z0-9_+#.-]{1,24}$/.test(word) ? word : "",
	};
}

function fenceCloses(line: string, ch: string, len: number): boolean {
	const m = /^[ \t]*(`{3,}|~{3,})[ \t]*$/.exec(line);
	return !!m && grp(m, 1)[0] === ch && grp(m, 1).length >= len;
}

/** Splits one table row on unescaped pipes; `\|` is a literal pipe. */
export function splitRow(line: string): string[] {
	const s = line.trim();
	const cells: string[] = [];
	let cur = "";
	let endedOnPipe = false;
	for (let i = s.startsWith("|") ? 1 : 0; i < s.length; i++) {
		endedOnPipe = false;
		if (s[i] === "\\" && s[i + 1] === "|") {
			cur += "|";
			i++;
		} else if (s[i] === "|") {
			cells.push(cur.trim());
			cur = "";
			endedOnPipe = true;
		} else cur += s[i];
	}
	if (!endedOnPipe) cells.push(cur.trim());
	return cells;
}

function isTableStart(lines: string[], i: number): boolean {
	const head = ln(lines, i);
	const delim = ln(lines, i + 1);
	if (head === undefined || delim === undefined || !head.includes("|") || !delim.includes("|")) return false;
	if (!DELIM.test(delim.trim())) return false;
	return splitRow(head).length === splitRow(delim).length;
}

function isBlockStart(lines: string[], i: number): boolean {
	const l = ln(lines, i);
	return fenceOpen(l) !== null || HEADING.test(l) || HR.test(l) || QUOTE.test(l) || isTableStart(lines, i);
}

function startsList(line: string, interrupting: boolean): boolean {
	if (HR.test(line)) return false;
	const m = ITEM.exec(line);
	if (!m) return false;
	if (!interrupting) return true;
	const marker = grp(m, 2);
	return !/\d/.test(marker) || Number.parseInt(marker, 10) === 1;
}

/** Block structure; inline content stays as source strings (see `toBlocks`). */
export function parseBlocks(lines: string[], depth = 0): RawBlock[] {
	const out: RawBlock[] = [];
	let i = 0;
	while (i < lines.length) {
		const line = ln(lines, i);
		if (line.trim() === "") {
			i++;
			continue;
		}
		const fence = fenceOpen(line);
		if (fence) {
			const body: string[] = [];
			i++;
			while (i < lines.length && !fenceCloses(ln(lines, i), fence.ch, fence.len)) {
				body.push(ln(lines, i).replace(new RegExp(`^[ \\t]{0,${fence.indent}}`), ""));
				i++;
			}
			i++;
			out.push({ t: "code", lang: fence.lang, v: body.join("\n") });
			continue;
		}
		const h = HEADING.exec(line);
		if (h) {
			out.push({ t: "h", level: Math.min(grp(h, 1).length, 4) as 1 | 2 | 3 | 4, c: grp(h, 2) });
			i++;
			continue;
		}
		if (HR.test(line)) {
			out.push({ t: "hr" });
			i++;
			continue;
		}
		if (QUOTE.test(line) && depth < MAX_QUOTE_DEPTH) {
			const inner: string[] = [];
			while (i < lines.length && QUOTE.test(ln(lines, i))) {
				inner.push(ln(lines, i).replace(QUOTE, ""));
				i++;
			}
			out.push({ t: "quote", c: parseBlocks(inner, depth + 1) });
			continue;
		}
		if (startsList(line, false)) {
			const r = parseList(lines, i);
			out.push(r.block);
			i = r.next;
			continue;
		}
		if (isTableStart(lines, i)) {
			const align: MdAlign[] = splitRow(ln(lines, i + 1)).map((c) => {
				const left = c.startsWith(":");
				const right = c.endsWith(":");
				return left && right ? "center" : right ? "right" : left ? "left" : null;
			});
			const width = align.length;
			const pad = (cells: string[]) => Array.from({ length: width }, (_, k) => cells[k] ?? "");
			const head = pad(splitRow(line));
			const rows: string[][] = [];
			i += 2;
			while (
				i < lines.length &&
				ln(lines, i).trim() !== "" &&
				ln(lines, i).includes("|") &&
				fenceOpen(ln(lines, i)) === null
			) {
				rows.push(pad(splitRow(ln(lines, i))));
				i++;
			}
			out.push({ t: "table", align, head, rows });
			continue;
		}
		const para: string[] = [line.trim()];
		i++;
		while (
			i < lines.length &&
			ln(lines, i).trim() !== "" &&
			!isBlockStart(lines, i) &&
			!startsList(ln(lines, i), true)
		) {
			para.push(ln(lines, i).trim());
			i++;
		}
		out.push({ t: "p", c: para.join("\n") });
	}
	return out;
}

function parseList(lines: string[], start: number): { block: RawBlock; next: number } {
	const first = ITEM.exec(ln(lines, start)) as RegExpExecArray;
	const base = indentOf(grp(first, 1));
	const ordered = /\d/.test(grp(first, 2));
	const firstNo = ordered ? Number.parseInt(grp(first, 2), 10) : 1;
	const items: Item<string>[] = [];
	let sub: { t: "list"; ordered: boolean; start: number; items: Item<string>[] } | null = null;
	let cur: Item<string> | null = null;
	let i = start;
	while (i < lines.length) {
		const line = ln(lines, i);
		if (line.trim() === "") {
			let k = i;
			while (k < lines.length && ln(lines, k).trim() === "") k++;
			if (k >= lines.length) break;
			const m = ITEM.exec(ln(lines, k));
			const cont = m ? true : indentOf(ln(lines, k)) >= base + 2 && !isBlockStart(lines, k);
			if (!cont) break;
			i = k;
			continue;
		}
		const m = HR.test(line) ? null : ITEM.exec(line);
		if (m) {
			const ind = indentOf(grp(m, 1));
			const isOrdered = /\d/.test(grp(m, 2));
			if (ind >= base + 2 && cur) {
				const item: Item<string> = { c: grp(m, 3), sub: null };
				if (!sub) {
					sub = { t: "list", ordered: isOrdered, start: isOrdered ? Number.parseInt(grp(m, 2), 10) : 1, items: [] };
					// the nested list belongs to the last top-level item
					const owner = items[items.length - 1];
					if (owner) owner.sub = sub;
				}
				sub.items.push(item);
				cur = item;
			} else {
				if (isOrdered !== ordered) break;
				sub = null;
				cur = { c: grp(m, 3), sub: null };
				items.push(cur);
			}
			i++;
			continue;
		}
		if (isBlockStart(lines, i)) break;
		if (cur) cur.c += `\n${line.trim()}`;
		i++;
	}
	return { block: { t: "list", ordered, start: ordered ? firstNo : 1, items }, next: i };
}

/** A line that may still become a table row: edge pipes, or short cells with no sentence in them. */
function headerLike(line: string): boolean {
	const t = line.trim();
	if (!t.includes("|")) return false;
	if (t.startsWith("|") || t.endsWith("|")) return true;
	if (t.length > 80) return false;
	return t.split("|").every((cell) => !/[.…!?,;:]/.test(cell) && cell.trim().split(/\s+/).length <= 2);
}

/** Provisional-rendering hold-backs for the last block of a message that is still arriving. */
function holdBack(blocks: RawBlock[], text: string): RawBlock[] {
	const last = blocks[blocks.length - 1];
	if (!last) return blocks;
	if (last.t === "table") {
		if (!text.endsWith("\n") && last.rows.length > 0) last.rows.pop();
	} else if (last.t === "p") {
		const lines = last.c.split("\n");
		let hold = 0;
		while (hold < 2 && hold < lines.length && headerLike(ln(lines, lines.length - 1 - hold))) hold++;
		if (hold >= lines.length) blocks.pop();
		else if (hold > 0) last.c = lines.slice(0, lines.length - hold).join("\n");
	}
	return blocks;
}

function mapBlock(b: RawBlock, inl: (s: string) => MdInline[]): MdBlock {
	switch (b.t) {
		case "p":
			return { t: "p", c: inl(b.c) };
		case "h":
			return { t: "h", level: b.level, c: inl(b.c) };
		case "list":
			return {
				t: "list",
				ordered: b.ordered,
				start: b.start,
				items: b.items.map((it) => ({
					c: inl(it.c),
					sub: it.sub ? mapBlock(it.sub, inl) : null,
				})),
			};
		case "quote":
			return { t: "quote", c: b.c.map((x) => mapBlock(x, inl)) };
		case "table":
			return { t: "table", align: b.align, head: b.head.map(inl), rows: b.rows.map((r) => r.map(inl)) };
		default:
			return b;
	}
}

/** Strips a citation that has only started (`[1`, `【`, `^`) from text that is still arriving. */
export function trimPartialCite(text: string): string {
	return text.replace(/(?:\[\^?\d{0,2}(?:[ \t]*[,，–—-][ \t]*\d{0,2})*[ \t]*|【[ \t]*\d{0,2}[ \t]*|\^)$/, "");
}

/** Markdown (the shared GFM subset) to blocks. Raw HTML is never interpreted: every `<` is text. */
export function parseMarkdown(text: string, o: ParseOptions = {}): MdBlock[] {
	let src = clean(text);
	if (o.streaming) src = trimPartialCite(src);
	let raw = parseBlocks(src.split("\n"));
	if (o.streaming) raw = holdBack(raw, src);
	const sources = o.sources ? new Set(o.sources) : null;
	return raw.map((b, k) => {
		const open = !!o.streaming && k === raw.length - 1;
		return mapBlock(b, (s) => parseInline(s, { sources, open, internal: !!o.internalLinks }));
	});
}

/** Inline-only Markdown (a notice line): no block syntax, newlines become breaks. */
export function parseInlineMarkdown(text: string, o: ParseOptions = {}): MdInline[] {
	const sources = o.sources ? new Set(o.sources) : null;
	return parseInline(clean(text).trim(), { sources, open: !!o.streaming, internal: !!o.internalLinks });
}
