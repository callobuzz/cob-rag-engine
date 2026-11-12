import { parseMarkdown } from "./parse";
import type { MdBlock, MdInline } from "./types";

function inline(nodes: readonly MdInline[]): string {
	return nodes
		.map((n) => {
			switch (n.t) {
				case "text":
				case "code":
					return n.v;
				case "br":
					return " ";
				case "cite":
					return "";
				default:
					return inline(n.c);
			}
		})
		.join("");
}

function block(b: MdBlock): string[] {
	switch (b.t) {
		case "p":
		case "h":
			return [inline(b.c)];
		case "code":
			return [b.v];
		case "list":
			return b.items.flatMap((i) => [inline(i.c), ...(i.sub ? block(i.sub) : [])]);
		case "quote":
			return b.c.flatMap(block);
		case "hr":
			return [];
		case "table":
			return [b.head, ...b.rows].map((r) => r.map(inline).join(", "));
	}
}

/** Markdown as the words a screen reader should speak: no markers, no citation numbers. */
export function toPlainText(text: string): string {
	return parseMarkdown(text)
		.flatMap(block)
		.map((s) =>
			s
				.replace(/\s+/g, " ")
				.replace(/ ([.,;:!?])/g, "$1")
				.trim(),
		)
		.filter(Boolean)
		.join(" ");
}
