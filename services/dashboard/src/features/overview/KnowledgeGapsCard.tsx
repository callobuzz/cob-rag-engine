import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { buttonVariants } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import type { Corpus } from "@/lib/api/queries";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { type GapReport, latestGapsQuery } from "./api";
import { HeadLink, List, Muted, Row } from "./ui";

const t = overviewStrings.gaps;

/** The biggest clusters of the latest completed report, most-asked first. */
export function topClusters(report: GapReport | undefined, n = 3) {
	if (report?.status !== "completed") return [];
	return [...(report.clusters ?? [])].sort((a, b) => b.query_count - a.query_count).slice(0, n);
}

/**
 * Latest gap report's top clusters (`/analytics/gaps?limit=1`; the list response carries the
 * clusters). "Add content" opens Add content for the report's knowledge base.
 */
export function KnowledgeGapsCard({ kbNames }: { kbNames: Map<string, Corpus> }) {
	const q = useQuery(latestGapsQuery);
	const report = q.data?.items[0];
	const clusters = topClusters(report);
	const kb = report?.corpus_id ? kbNames.get(report.corpus_id) : undefined;
	return (
		<Panel
			title={t.title}
			sub={t.sub}
			aside={
				<HeadLink to="/insights" label={t.allLabel}>
					{t.all}
				</HeadLink>
			}
		>
			{q.isPending ? (
				<TableSkeleton rows={3} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
				</div>
			) : !report ? (
				<div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3.5 text-[13px] text-muted-foreground">
					<span>{t.none}</span>
					<Link to="/insights" className={buttonVariants({ size: "sm" })}>
						{t.run}
					</Link>
				</div>
			) : report.status === "failed" ? (
				<Muted>{t.failed}</Muted>
			) : report.status !== "completed" ? (
				<Muted>{t.running}</Muted>
			) : clusters.length === 0 ? (
				<Muted>{t.clean}</Muted>
			) : (
				<List label={t.title}>
					{clusters.map((c) => {
						const q0 = c.representative_queries[0] ?? c.keywords.join(", ");
						return (
							<Row
								key={c.topic_id}
								wrap
								testId="gap-row"
								title={`“${q0}”`}
								sub={[kb?.name, fill(t.times, { n: formatNumber(c.query_count) }), c.keywords.slice(0, 3).join(", ")]
									.filter(Boolean)
									.join(" · ")}
								trail={
									<Link
										to="/add"
										search={kb ? { kb: kb.id } : {}}
										aria-label={fill(t.addContentLabel, { q: q0 })}
										className={buttonVariants({ size: "sm" })}
									>
										{t.addContent}
									</Link>
								}
							/>
						);
					})}
				</List>
			)}
		</Panel>
	);
}
