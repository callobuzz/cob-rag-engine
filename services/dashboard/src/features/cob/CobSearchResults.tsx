import { cobStrings as t } from "@/lib/strings/cob";
import { sourceTarget, useCobLinks } from "./links";
import type { CobSearchResultsBlock } from "./types";

/** Search mode (at the question cap, or when no answer model is reachable): matching help pages, no written answer. */
export function CobSearchResults({ block }: { block: CobSearchResultsBlock }) {
	const { hasDocs, onInternalClick } = useCobLinks();
	return (
		<section className="grid gap-2" data-testid="cob-search-results" aria-label={t.sources.label}>
			<p
				role="note"
				className="m-0 rounded-md border border-border bg-secondary px-3 py-2 text-[12.5px] text-foreground-2"
			>
				{t.search.banner[block.reason] ?? t.search.banner.docs_only}
			</p>
			{block.items.length === 0 ? (
				<p className="m-0 text-[13px] text-muted-foreground">{t.search.empty}</p>
			) : (
				<ul className="m-0 grid list-none gap-2 p-0">
					{block.items.map((s) => {
						const target = sourceTarget(s, hasDocs);
						return (
							<li key={`${s.slug}#${s.anchor}`} className="grid gap-1 rounded-md border border-border bg-card p-3">
								<span className="text-[13px] font-semibold">{s.title}</span>
								<span className="text-[12.5px] text-foreground-2">{s.snippet}</span>
								{target?.kind === "internal" ? (
									<a
										href={target.href}
										onClick={onInternalClick(target.href)}
										className="w-fit text-[12.5px] font-semibold text-brand-text underline-offset-4 hover:underline"
									>
										{t.search.open}
									</a>
								) : target?.kind === "external" ? (
									<a
										href={target.url}
										target="_blank"
										rel="noopener noreferrer"
										className="w-fit text-[12.5px] font-semibold text-brand-text underline-offset-4 hover:underline"
									>
										{t.search.open}
									</a>
								) : null}
							</li>
						);
					})}
				</ul>
			)}
		</section>
	);
}
