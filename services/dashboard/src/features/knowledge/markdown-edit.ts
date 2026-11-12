/**
 * The three formatting buttons of the article editor (Heading, Bullet, Bold), as pure functions on
 * the text and the selection, so they can be tested without a browser. Each returns the new text
 * and where the selection should be afterwards.
 */
export type Format = "heading" | "bullet" | "bold";

export interface Edit {
	text: string;
	start: number;
	end: number;
}

/** The start of the line holding `index` and the end of the line holding `end`. */
function lineSpan(text: string, start: number, end: number): [number, number] {
	const from = text.lastIndexOf("\n", Math.max(0, start) - 1) + 1;
	const next = text.indexOf("\n", end);
	return [from, next === -1 ? text.length : next];
}

function prefixLines(text: string, start: number, end: number, prefix: string, strip: RegExp, same: RegExp): Edit {
	const [from, to] = lineSpan(text, start, end);
	const lines = text.slice(from, to).split("\n");
	const allHave = lines.every((l) => l.trim() === "" || same.test(l));
	const changed = lines
		.map((l) => {
			if (l.trim() === "") return l;
			return allHave ? l.replace(strip, "") : `${prefix}${l.replace(strip, "")}`;
		})
		.join("\n");
	const delta = changed.length - (to - from);
	return {
		text: text.slice(0, from) + changed + text.slice(to),
		start: Math.max(from, start + (allHave ? -prefix.length : prefix.length)),
		end: end + delta,
	};
}

export function applyFormat(text: string, start: number, end: number, kind: Format): Edit {
	if (kind === "heading") return prefixLines(text, start, end, "## ", /^#{1,6}\s+/, /^##\s+/);
	if (kind === "bullet") return prefixLines(text, start, end, "- ", /^[-*]\s+/, /^[-*]\s+/);
	const selected = text.slice(start, end);
	if (selected.startsWith("**") && selected.endsWith("**") && selected.length >= 4) {
		const inner = selected.slice(2, -2);
		return { text: text.slice(0, start) + inner + text.slice(end), start, end: start + inner.length };
	}
	const body = selected || "bold";
	const wrapped = `**${body}**`;
	return { text: text.slice(0, start) + wrapped + text.slice(end), start: start + 2, end: start + 2 + body.length };
}
