import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CircleAlert } from "lucide-react";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { docsStrings as t } from "@/lib/strings/docs";
import { fill } from "@/lib/strings/en";
import { docsTreeQuery, filterTitles, SEARCH_MIN, useDocsSearch } from "./api";

/**
 * Search results, one row per page with its best section first. When the engine cannot search
 * (the knowledge base behind it is down) the pages whose title matches are shown instead, with a
 * notice, so the Docs stay usable.
 */
export function DocsResults({ q }: { q: string }) {
	const tree = useQuery(docsTreeQuery);
	const search = useDocsSearch(q);
	const short = q.trim().length < SEARCH_MIN;
	const fallback = search.isError;
	const items = fallback ? filterTitles(tree.data, q) : (search.data?.items ?? []);

	return (
		<section aria-labelledby="docs-results-title" className="grid gap-3">
			<h2 id="docs-results-title" className="m-0 text-lg font-bold">
				{fill(t.searchResults, { q: q.trim() })}
			</h2>
			{fallback ? (
				<p
					role="status"
					className="m-0 flex items-start gap-2 rounded-md bg-warning-soft p-3 text-[13px] text-foreground"
				>
					<CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
					{t.searchFallback}
				</p>
			) : null}
			{short && !fallback ? (
				<p className="m-0 text-foreground-2">{t.searchTooShort}</p>
			) : search.isFetching && items.length === 0 && !fallback ? (
				<div role="status" aria-busy="true">
					<span className="sr-only">{t.searching}</span>
					<CardSkeleton lines={3} />
				</div>
			) : items.length === 0 ? (
				<p className="m-0 text-foreground-2">{fill(t.searchNone, { q: q.trim() })}</p>
			) : (
				<ul className="m-0 grid list-none gap-2 p-0">
					{items.map((item) => (
						<li key={`${item.slug}#${item.anchor}`}>
							<Link
								to="/docs/$"
								params={{ _splat: item.slug }}
								hash={item.anchor || undefined}
								className="block rounded-md border border-border bg-card p-3 no-underline hover:bg-accent"
							>
								<span className="block font-semibold text-foreground">{item.title}</span>
								{item.snippet ? (
									<span className="mt-0.5 block text-[13px] text-foreground-2">{item.snippet}</span>
								) : null}
							</Link>
						</li>
					))}
				</ul>
			)}
		</section>
	);
}
