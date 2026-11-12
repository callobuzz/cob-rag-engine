import { CircleAlert } from "lucide-react";
import { Panel } from "@/features/kbs/ui";
import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { cn } from "@/lib/utils";
import type { ScoreBreakdown, StreamSource } from "./api";
import { maxima, ScoreBars, SIGNALS, signalValue } from "./ScoreBars";

/** The id a citation chip moves focus to. */
export const sourceDomId = (prefix: string, index: number) => `${prefix}-src-${index}`;

export function sourceName(s: Pick<StreamSource, "file_name" | "external_id" | "document_id">): string {
	return s.file_name || s.external_id || s.document_id || "—";
}

/** Each source's breakdown by `chunk_id` (it rides the stream's `sources` event; missing → null). */
export function breakdownsOf(sources: StreamSource[]): Map<string, ScoreBreakdown | null> {
	return new Map(sources.map((s) => [s.chunk_id, s.score_breakdown ?? null]));
}

/** Mockup Sources card: each passage given to the model with its scores. */
export function SourcesPanel({
	sources,
	scoresState,
	highlight,
	idPrefix,
	sub,
}: {
	sources: StreamSource[];
	scoresState: "pending" | "ok" | "error" | "skipped";
	highlight: number | null;
	idPrefix: string;
	sub?: string;
}) {
	const joined = breakdownsOf(sources);
	const max = maxima([...joined.values()]);
	const present = SIGNALS.filter((s) => [...joined.values()].some((b) => b && signalValue(b, s.key) !== null));
	const legend = present.length ? present : SIGNALS.filter((s) => s.key !== "rerank");
	return (
		<Panel
			title={t.sources.title}
			sub={sub ?? t.sources.sub}
			aside={
				<div className="flex flex-wrap gap-3 text-xs text-muted-foreground" aria-hidden>
					{legend.map((s) => (
						<span key={s.key} className="inline-flex items-center gap-1.5">
							<i className="size-2 rounded-[2px]" style={{ background: s.color }} />
							{s.label}
						</span>
					))}
				</div>
			}
		>
			{scoresState === "error" && sources.length ? (
				<p className="m-0 flex items-start gap-2 border-b border-border px-4 py-2.5 text-[12.5px] text-warning">
					<CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
					{t.sources.unavailable}
				</p>
			) : null}
			{sources.length === 0 ? (
				<p className="m-0 px-4 py-6 text-[13px] text-muted-foreground">{t.sources.empty}</p>
			) : (
				<ol aria-label={t.sources.listLabel} className="m-0 list-none p-0">
					{sources.map((s) => {
						const b = joined.get(s.chunk_id) ?? null;
						return (
							<li
								key={`${s.chunk_id}-${s.index}`}
								id={sourceDomId(idPrefix, s.index)}
								tabIndex={-1}
								data-testid="source"
								className={cn(
									"grid gap-1.5 border-b border-border px-4 py-3 outline-none last:border-b-0 focus-visible:bg-accent",
									highlight === s.index && "bg-primary-soft",
								)}
							>
								<div className="flex min-w-0 items-center gap-2">
									<span className="font-mono text-xs text-primary">[{s.index}]</span>
									<span className="truncate font-semibold">{sourceName(s)}</span>
									{s.section ? (
										<span className="truncate text-[12.5px] whitespace-nowrap text-muted-foreground">
											· {s.section}
										</span>
									) : null}
									{s.page != null ? (
										<span className="shrink-0 text-[12.5px] text-muted-foreground">
											· {fill(t.sources.page, { n: s.page })}
										</span>
									) : null}
								</div>
								<p className="m-0 text-[13px] break-words text-foreground-2">{s.snippet}</p>
								{b ? (
									<ScoreBars breakdown={b} max={max} />
								) : scoresState === "ok" ? (
									<span className="text-[11.5px] text-muted-foreground">{t.sources.noBreakdown}</span>
								) : null}
							</li>
						);
					})}
				</ol>
			)}
			{scoresState === "ok" && sources.length ? (
				<p className="m-0 border-t border-border px-4 py-2 text-[11.5px] text-muted-foreground">{t.sources.matching}</p>
			) : null}
		</Panel>
	);
}
