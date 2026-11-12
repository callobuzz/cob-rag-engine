import { clean, fenceOpen, indentOf, ln } from "./parse";

const LIST_LINE = /^[ \t]*(?:[-*+•]|\d{1,9}[.)])[ \t]+\S/;

/**
 * Offsets that end a stable chunk: just after the first blank line that follows content, outside a
 * code fence, and only when the next non-blank line is already complete and cannot continue a list
 * (a list marker or an indented line might). A chunk ending there parses the same on its own as
 * inside the whole text, so it is never re-rendered while the rest of the answer arrives.
 */
export function boundaries(text: string): number[] {
	const lines = text.split("\n");
	const out: number[] = [];
	let offset = 0;
	let fence: { ch: string; len: number } | null = null;
	let seenContent = false;
	let pending = -1;
	for (let n = 0; n < lines.length; n++) {
		const line = ln(lines, n);
		offset += line.length + 1;
		if (fence) {
			const m = /^[ \t]*(`{3,}|~{3,})[ \t]*$/.exec(line);
			const run = m?.[1] ?? "";
			if (run.charAt(0) === fence.ch && run.length >= fence.len) fence = null;
			continue;
		}
		if (line.trim() === "") {
			if (seenContent && pending < 0) pending = offset;
			continue;
		}
		if (pending >= 0) {
			// a line still arriving (no newline yet) might become a list marker: not decided
			if (n < lines.length - 1 && !LIST_LINE.test(line) && indentOf(line) < 2) out.push(pending);
			pending = -1;
		}
		seenContent = true;
		const f = fenceOpen(line);
		if (f) fence = { ch: f.ch, len: f.len };
	}
	return out;
}

/** The text up to the last stable boundary, and the rest (the tail, still changing). */
export function splitStable(text: string): { stable: string; tail: string } {
	const s = clean(text);
	const b = boundaries(s);
	const at = b.length ? b[b.length - 1] : 0;
	return { stable: s.slice(0, at), tail: s.slice(at) };
}

/** Stable chunks (each a whole number of blocks) followed by the tail. */
export function chunks(text: string): { stable: string[]; tail: string } {
	const s = clean(text);
	const b = boundaries(s);
	const stable: string[] = [];
	let from = 0;
	for (const at of b) {
		stable.push(s.slice(from, at));
		from = at;
	}
	return { stable, tail: s.slice(from) };
}
