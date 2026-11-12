import { Check, Copy } from "lucide-react";
import { Fragment, memo, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import "@/styles/markdown.css";
import { markdownStrings as t } from "@/lib/strings/markdown";
import { cn } from "@/lib/utils";
import { parseInlineMarkdown, parseMarkdown } from "./parse";
import { Anchors, slugify } from "./slug";
import { chunks } from "./stream";
import type { MdAlign, MdBlock, MdInline } from "./types";

interface PageCtx {
	/** The id of each top-level heading block (see `assignIds`). */
	ids: WeakMap<object, string>;
	onNavigate?: (href: string) => void;
}

interface Ctx {
	onCite?: (n: number) => void;
	copyable: boolean;
	/** Set for `variant="page"`. */
	page?: PageCtx;
}

function plain(nodes: MdInline[]): string {
	return nodes
		.map((n) => {
			switch (n.t) {
				case "text":
				case "code":
					return n.v;
				case "strong":
				case "em":
				case "del":
				case "link":
					return plain(n.c);
				default:
					return "";
			}
		})
		.join("");
}

/**
 * One id per top-level heading. The engine's own anchors (`headingIds`) go to the level-2
 * headings in order, because a page built from an HTML guide keeps its element ids, which are not
 * a slug of the title; everything else is numbered the way the engine numbers its anchors.
 */
function assignIds(blocks: MdBlock[], headingIds: readonly string[] = []): WeakMap<object, string> {
	const anchors = new Anchors();
	const ids = new WeakMap<object, string>();
	let h2 = 0;
	for (const b of blocks) {
		if (b.t !== "h") continue;
		const given = b.level === 2 ? headingIds[h2++] : undefined;
		ids.set(b, given ?? anchors.take(slugify(plain(b.c))));
	}
	return ids;
}

function Inline({ nodes, ctx }: { nodes: MdInline[]; ctx: Ctx }): ReactNode {
	return nodes.map((n, i) => {
		const key = i;
		switch (n.t) {
			case "text":
				return <Fragment key={key}>{n.v}</Fragment>;
			case "code":
				return <code key={key}>{n.v}</code>;
			case "br":
				return <br key={key} />;
			case "strong":
				return (
					<strong key={key}>
						<Inline nodes={n.c} ctx={ctx} />
					</strong>
				);
			case "em":
				return (
					<em key={key}>
						<Inline nodes={n.c} ctx={ctx} />
					</em>
				);
			case "del":
				return (
					<del key={key}>
						<Inline nodes={n.c} ctx={ctx} />
					</del>
				);
			case "link":
				if (ctx.page && (n.href.startsWith("/") || n.href.startsWith("#"))) {
					const { href } = n;
					return (
						<a
							key={key}
							href={href}
							onClick={(e) => {
								if (!ctx.page?.onNavigate || e.defaultPrevented || e.button !== 0) return;
								if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
								e.preventDefault();
								ctx.page.onNavigate(href);
							}}
						>
							<Inline nodes={n.c} ctx={ctx} />
						</a>
					);
				}
				return (
					<a key={key} href={n.href} target="_blank" rel="noopener noreferrer nofollow">
						<Inline nodes={n.c} ctx={ctx} />
					</a>
				);
			case "cite":
				return (
					<button
						key={key}
						type="button"
						className="md-cite"
						data-cite={n.n}
						onClick={ctx.onCite ? () => ctx.onCite?.(n.n) : undefined}
					>
						{n.n}
					</button>
				);
			default:
				return null;
		}
	});
}

const HEADINGS = ["h3", "h4", "h5", "h6"] as const;
const PAGE_HEADINGS = ["h1", "h2", "h3", "h4"] as const;

function CodeBlock({
	lang,
	value,
	copyable,
	scrollable,
}: {
	lang: string;
	value: string;
	copyable: boolean;
	/** Docs pages: a long line scrolls, so the block must be reachable with the keyboard. */
	scrollable?: boolean;
}) {
	const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
	const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
	useEffect(() => () => clearTimeout(timer.current), []);
	const code = <code className={lang ? `lang-${lang}` : undefined}>{value}</code>;
	const pre = scrollable ? (
		// biome-ignore lint/a11y/noNoninteractiveTabindex: a keyboard user must be able to scroll a wide code block
		<pre tabIndex={0}>{code}</pre>
	) : (
		<pre>{code}</pre>
	);
	if (!copyable) return pre;
	const copy = async () => {
		try {
			await navigator.clipboard.writeText(value);
			setState("copied");
		} catch {
			setState("failed");
		}
		clearTimeout(timer.current);
		timer.current = setTimeout(() => setState("idle"), 1800);
	};
	const label = state === "copied" ? t.copied : state === "failed" ? t.copyFailed : t.copy;
	return (
		<div className="md-code">
			{pre}
			<button type="button" className="md-copy" onClick={() => void copy()} aria-label={label} title={label}>
				{state === "copied" ? <Check aria-hidden /> : <Copy aria-hidden />}
			</button>
		</div>
	);
}

function alignClass(a: MdAlign): string | undefined {
	return a ? `md-${a}` : undefined;
}

function Blocks({ blocks, ctx }: { blocks: MdBlock[]; ctx: Ctx }): ReactNode {
	return blocks.map((b, i) => {
		const key = i;
		switch (b.t) {
			case "p":
				return (
					<p key={key}>
						<Inline nodes={b.c} ctx={ctx} />
					</p>
				);
			case "h": {
				if (ctx.page) {
					const P = PAGE_HEADINGS[b.level - 1] ?? "h4";
					return (
						<P key={key} id={ctx.page.ids.get(b)}>
							<Inline nodes={b.c} ctx={ctx} />
						</P>
					);
				}
				const H = HEADINGS[b.level - 1] ?? "h6";
				return (
					<H key={key}>
						<Inline nodes={b.c} ctx={ctx} />
					</H>
				);
			}
			case "code":
				return <CodeBlock key={key} lang={b.lang} value={b.v} copyable={ctx.copyable} scrollable={!!ctx.page} />;
			case "list": {
				const items = b.items.map((it, k) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: items are positional and never reorder
					<li key={k}>
						<Inline nodes={it.c} ctx={ctx} />
						{it.sub ? <Blocks blocks={[it.sub]} ctx={ctx} /> : null}
					</li>
				));
				return b.ordered ? (
					<ol key={key} start={b.start !== 1 ? b.start : undefined}>
						{items}
					</ol>
				) : (
					<ul key={key}>{items}</ul>
				);
			}
			case "quote":
				return (
					<blockquote key={key}>
						<Blocks blocks={b.c} ctx={ctx} />
					</blockquote>
				);
			case "hr":
				return <hr key={key} />;
			case "table":
				return (
					// biome-ignore lint/a11y/useSemanticElements: a scrollable table wrapper, not a <section>
					// biome-ignore lint/a11y/noNoninteractiveTabindex: a keyboard user must be able to scroll a wide table
					<div key={key} className="md-table" role="region" tabIndex={0} aria-label={t.table}>
						<table>
							<thead>
								<tr>
									{b.head.map((c, k) => (
										// biome-ignore lint/suspicious/noArrayIndexKey: columns are positional
										<th key={k} scope="col" className={alignClass(b.align[k] ?? null)}>
											<Inline nodes={c} ctx={ctx} />
										</th>
									))}
								</tr>
							</thead>
							{b.rows.length ? (
								<tbody>
									{b.rows.map((r, ri) => (
										// biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
										<tr key={ri}>
											{r.map((c, k) => (
												// biome-ignore lint/suspicious/noArrayIndexKey: columns are positional
												<td key={k} className={alignClass(b.align[k] ?? null)}>
													<Inline nodes={c} ctx={ctx} />
												</td>
											))}
										</tr>
									))}
								</tbody>
							) : null}
						</table>
					</div>
				);
			default:
				return null;
		}
	});
}

const Stable = memo(function Stable({
	text,
	sourcesKey,
	onCite,
	copyable,
}: {
	text: string;
	sourcesKey: string | null;
	onCite: ((n: number) => void) | undefined;
	copyable: boolean;
}) {
	const blocks = useMemo(
		() => parseMarkdown(text, { sources: sourcesKey === null ? null : toNumbers(sourcesKey) }),
		[text, sourcesKey],
	);
	return <Blocks blocks={blocks} ctx={{ onCite, copyable }} />;
});

function toNumbers(key: string): number[] {
	return key ? key.split(",").map(Number) : [];
}

export interface MarkdownProps {
	text: string;
	/** The answer is still arriving: the last block is provisional, earlier ones never re-render. */
	streaming?: boolean;
	/** Inline only (a notice line): no paragraphs, no block syntax. */
	inline?: boolean;
	/** Source numbers a citation marker may point at; markers for others stay plain text. */
	sources?: readonly number[];
	/** A citation chip was clicked. Without it the chips are inert. */
	onCite?: (n: number) => void;
	/** Show the copy button on code blocks (default). */
	copyable?: boolean;
	/**
	 * `chat` (default): headings start at h3 and carry no id, links open in a new tab, only
	 * http(s), mailto and tel links. `page` (the Docs): headings are h1 to h4 with ids that match
	 * the engine's table-of-contents anchors, and links to `/docs/...` or `#anchor` stay in the app.
	 */
	variant?: "chat" | "page";
	/** Page variant: an in-app link was activated (no modifier key held). */
	onNavigate?: (href: string) => void;
	/** Page variant: the anchors of the level-2 headings, in order (the page's contents list). */
	headingIds?: readonly string[];
	className?: string;
}

/**
 * Renders model Markdown as React elements: never `dangerouslySetInnerHTML`, no raw HTML, link
 * targets pass `safeUrl`. Blocks before the last blank-line boundary are memoised by their text, so
 * a long answer streaming in only re-parses its tail.
 */
export function Markdown(props: MarkdownProps) {
	if (props.variant === "page" && !props.inline) {
		return (
			<PageMarkdown
				text={props.text}
				copyable={props.copyable ?? true}
				onNavigate={props.onNavigate}
				headingIds={props.headingIds}
				className={props.className}
			/>
		);
	}
	return <ChatMarkdown {...props} />;
}

function PageMarkdown({
	text,
	copyable,
	onNavigate,
	headingIds,
	className,
}: {
	text: string;
	copyable: boolean;
	onNavigate: ((href: string) => void) | undefined;
	headingIds: readonly string[] | undefined;
	className: string | undefined;
}) {
	const blocks = useMemo(() => parseMarkdown(text, { sources: [], internalLinks: true }), [text]);
	const idsKey = headingIds ? headingIds.join(",") : "";
	// biome-ignore lint/correctness/useExhaustiveDependencies: idsKey stands for headingIds
	const ids = useMemo(() => assignIds(blocks, headingIds), [blocks, idsKey]);
	const ctx: Ctx = { copyable, page: { ids, onNavigate } };
	return (
		<div className={cn("md md-page", className)}>
			<Blocks blocks={blocks} ctx={ctx} />
		</div>
	);
}

function ChatMarkdown({ text, streaming, inline, sources, onCite, copyable = true, className }: MarkdownProps) {
	const sourcesKey = sources ? sources.join(",") : null;
	const cite = useRef(onCite);
	cite.current = onCite;
	const handle = useCallback((n: number) => cite.current?.(n), []);
	const hasCite = !!onCite;
	const ctx: Ctx = { onCite: hasCite ? handle : undefined, copyable };

	const inlineNodes = useMemo(
		() =>
			inline
				? parseInlineMarkdown(text, {
						sources: sourcesKey === null ? null : toNumbers(sourcesKey),
						streaming,
					})
				: null,
		[inline, text, sourcesKey, streaming],
	);
	const split = useMemo(() => (inline ? null : chunks(text)), [inline, text]);
	const tail = useMemo(
		() =>
			split
				? parseMarkdown(split.tail, { sources: sourcesKey === null ? null : toNumbers(sourcesKey), streaming })
				: [],
		[split, sourcesKey, streaming],
	);

	if (inlineNodes) {
		return (
			<span className={cn("md md-inline", className)}>
				<Inline nodes={inlineNodes} ctx={ctx} />
			</span>
		);
	}
	return (
		<div className={cn("md", streaming && "md-streaming", className)}>
			{split?.stable.map((chunk, i) => (
				// biome-ignore lint/suspicious/noArrayIndexKey: stable chunks only ever append
				<Stable key={i} text={chunk} sourcesKey={sourcesKey} onCite={ctx.onCite} copyable={copyable} />
			))}
			<Blocks blocks={tail} ctx={ctx} />
		</div>
	);
}
