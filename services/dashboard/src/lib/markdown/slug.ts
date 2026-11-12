/**
 * Heading ids for the Docs page variant. Held identical to the engine's
 * `cob/sources/common.py:slugify` by `tests/fixtures/markdown/heading-ids.json`, which both test
 * suites read: lower case, NFKC, punctuation dropped (a letter or digit in any script stays; a
 * combining mark does not, as in Python's `\w`), spaces to hyphens.
 */
export function slugify(text: string): string {
	const base = text
		.normalize("NFKC")
		.trim()
		.toLowerCase()
		.replace(/[^\p{L}\p{N}_\- ]/gu, "")
		.replace(/ /g, "-")
		.replace(/-{2,}/g, "-")
		.replace(/^-+|-+$/g, "");
	return base || "section";
}

/** Hands out ids, repeats become `-2`, `-3`... (the engine's `Anchors`). */
export class Anchors {
	private seen = new Map<string, number>();

	take(wanted: string): string {
		const n = (this.seen.get(wanted) ?? 0) + 1;
		this.seen.set(wanted, n);
		return n === 1 ? wanted : `${wanted}-${n}`;
	}
}
