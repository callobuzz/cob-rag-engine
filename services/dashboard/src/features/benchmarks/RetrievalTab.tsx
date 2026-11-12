import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Panel } from "@/features/kbs/ui";
import type { components } from "@/lib/api/schema";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { benchmarkSummaryQuery } from "./api";

type SummarySection = components["schemas"]["BenchmarkSummarySection"];

/** BEIR, real documents, turn labels and time to first token: each summary section as a plain table. */
export function RetrievalTab() {
	const q = useQuery(benchmarkSummaryQuery);
	if (q.isPending) return <TableSkeleton rows={4} />;
	if (q.isError) return <ErrorState error={q.error} title={t.page.error} onRetry={() => void q.refetch()} />;
	const sections = q.data.summary?.sections ?? [];
	if (!q.data.available || sections.length === 0) {
		return <p className="m-0 text-[13px] text-muted-foreground">{t.page.emptyBody}</p>;
	}
	return (
		<div className="grid gap-4">
			<p className="m-0 max-w-[760px] text-[13px] text-foreground-2">{t.retrieval.lead}</p>
			{sections.map((s) => (
				<SummaryTable key={s.id} section={s} />
			))}
		</div>
	);
}

function SummaryTable({ section: s }: { section: SummarySection }) {
	return (
		<Panel
			title={s.title}
			sub={s.date}
			aside={
				<a
					href={s.link}
					target="_blank"
					rel="noopener noreferrer"
					className="inline-flex items-center gap-1 text-[13px] text-primary hover:underline"
				>
					{t.page.method}
					<ExternalLink className="size-3.5" aria-hidden />
				</a>
			}
		>
			<p className="m-0 max-w-[900px] px-4 pt-3 text-[13px] text-foreground-2">{s.method}</p>
			<section
				// biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable table region must be reachable by keyboard
				tabIndex={0}
				aria-label={fill(t.tableRegion, { name: s.title })}
				className="mt-3 overflow-x-auto border-t border-border focus-visible:outline-2 focus-visible:outline-ring"
			>
				<table className="w-full border-collapse text-[13px]">
					<caption className="sr-only">{s.title}</caption>
					<thead>
						<tr>
							<th scope="col" className="density-cell border-b border-border text-left" />
							{s.columns.map((c) => (
								<th key={c} scope="col" className="density-cell border-b border-border text-right font-semibold">
									{c}
								</th>
							))}
						</tr>
					</thead>
					<tbody>
						{s.rows.map((r) => (
							<tr
								key={r.name}
								data-highlight={r.highlight || undefined}
								className={cn("border-b border-border last:border-b-0", r.highlight && "bg-primary-soft font-bold")}
							>
								<th scope="row" className="density-cell text-left font-[inherit]">
									{r.name}
								</th>
								{r.values.map((v, i) => (
									// biome-ignore lint/suspicious/noArrayIndexKey: values are positional, one per column
									<td key={i} className="density-cell text-right tabular-nums">
										{v}
									</td>
								))}
							</tr>
						))}
					</tbody>
				</table>
			</section>
		</Panel>
	);
}
