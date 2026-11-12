/**
 * The Markdown AST (conversation README, "The Markdown renderer contract"). The widget's
 * `markdown.js` builds the same structure as element nodes; `tests/fixtures/markdown/conformance.json`
 * keeps the two held identical.
 */
export type MdInline =
	| { t: "text"; v: string }
	| { t: "code"; v: string }
	| { t: "br" }
	| { t: "strong" | "em" | "del"; c: MdInline[] }
	| { t: "link"; href: string; c: MdInline[] }
	| { t: "cite"; n: number };

export type MdAlign = "left" | "center" | "right" | null;

/** A block whose inline content is `I`: a source string before inline parsing, `MdInline[]` after. */
export type Block<I> =
	| { t: "p"; c: I }
	| { t: "h"; level: 1 | 2 | 3 | 4; c: I }
	| { t: "code"; lang: string; v: string }
	| { t: "list"; ordered: boolean; start: number; items: Item<I>[] }
	| { t: "quote"; c: Block<I>[] }
	| { t: "hr" }
	| { t: "table"; align: MdAlign[]; head: I[]; rows: I[][] };

export interface Item<I> {
	c: I;
	/** One level of nesting only; items of a nested list never have their own. */
	sub: Block<I> | null;
}

export type MdBlock = Block<MdInline[]>;
export type RawBlock = Block<string>;

export interface ParseOptions {
	/** Source numbers a citation marker may point at; undefined accepts every marker. */
	sources?: readonly number[] | null;
	/** Docs pages: a link to a path on this site (`/docs/...`) or an anchor (`#x`) is kept. */
	internalLinks?: boolean;
	/** Provisional rendering of a message that is still arriving (hold-backs, closing markers). */
	streaming?: boolean;
}
