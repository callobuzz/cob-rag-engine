import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { FileWarning } from "lucide-react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { isTerminal } from "@/features/add-content/api";
import { stageLabel } from "@/features/add-content/JobStages";
import { JobStatusBadge, jobName } from "@/features/add-content/JobsTable";
import { Panel } from "@/features/kbs/ui";
import type { Corpus } from "@/lib/api/queries";
import { formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { recentJobsQuery } from "./api";
import { HeadLink, List, Muted, Row } from "./ui";

const t = overviewStrings.jobs;

/** The five newest uploads and imports; running ones with progress, "Review" for near-duplicates. */
export function RecentJobsCard({ kbNames }: { kbNames: Map<string, Corpus> }) {
	const q = useQuery(recentJobsQuery);
	const items = q.data?.items ?? [];
	return (
		<Panel
			title={t.title}
			sub={t.sub}
			aside={
				<HeadLink to="/add" search={{ tab: "jobs" }} label={t.allLabel}>
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
			) : items.length === 0 ? (
				<Muted>{t.none}</Muted>
			) : (
				<List label={t.title}>
					{items.map((j) => {
						const kb = j.corpus_id ? kbNames.get(j.corpus_id) : undefined;
						const running = !isTerminal(j.status);
						const total = j.total_documents ?? 0;
						const share = running && total > 0 ? Math.min(1, j.processed_documents / total) : null;
						return (
							<Row
								key={j.id}
								testId="job-row"
								title={
									<Link
										to="/add"
										search={{ kb: j.corpus_id ?? undefined, tab: "jobs" }}
										className="text-foreground no-underline hover:underline"
									>
										{jobName(j)}
									</Link>
								}
								sub={[
									kb?.name,
									running ? stageLabel(j.status) : null,
									share != null
										? fill(t.progress, { done: formatNumber(j.processed_documents), total: formatNumber(total) })
										: null,
									formatRelative(j.created_at),
								]
									.filter(Boolean)
									.join(" · ")}
								trail={
									j.status === "pending_review" ? (
										<Link to="/documents" search={{ kb: j.corpus_id ?? undefined }} className="no-underline">
											<Badge variant="warn">
												<FileWarning aria-hidden />
												{t.review}
											</Badge>
										</Link>
									) : (
										<JobStatusBadge status={j.status} />
									)
								}
							>
								{share != null ? (
									<div
										role="progressbar"
										aria-label={jobName(j)}
										aria-valuemin={0}
										aria-valuemax={total}
										aria-valuenow={j.processed_documents}
										className="mt-1.5 h-1.5 overflow-hidden rounded-[3px] bg-secondary"
									>
										<i
											className="block h-full rounded-[3px] bg-primary"
											style={{ width: `${Math.round(share * 100)}%` }}
										/>
									</div>
								) : null}
							</Row>
						);
					})}
				</List>
			)}
		</Panel>
	);
}
