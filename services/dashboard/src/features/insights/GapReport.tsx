import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, Upload } from "lucide-react";
import { FormAlert } from "@/components/auth/FormAlert";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import { type GapCluster, gapReportQuery, isTerminal } from "./api";
import { useKbNames } from "./ui";

const t = insightsStrings.gaps;

const plural = (w: { one: string; many: string }, n: number) =>
	n === 1 ? w.one : fill(w.many, { n: formatNumber(n) });

/** Clusters from a report (typed) or the admin "latest" report's loose `topics` objects. */
export function asClusters(raw: unknown[] | null | undefined): GapCluster[] {
	return (raw ?? [])
		.map((x, i): GapCluster => {
			const o = (x && typeof x === "object" ? x : {}) as Record<string, unknown>;
			const strings = (v: unknown) => (Array.isArray(v) ? v.filter((s): s is string => typeof s === "string") : []);
			return {
				topic_id: typeof o.topic_id === "number" ? o.topic_id : i,
				query_count: typeof o.query_count === "number" ? o.query_count : 0,
				keywords: strings(o.keywords),
				representative_queries: strings(o.representative_queries),
			};
		})
		.sort((a, b) => b.query_count - a.query_count);
}

/**
 * Topics of a report: keywords as chips, how often, example questions and "Add content for
 * this" → `/add?kb=` (the keywords are repeated in the link's description as the hint).
 */
export function ClusterList({ clusters, addKb }: { clusters: GapCluster[]; addKb: string }) {
	if (!clusters.length) return <p className="m-0 p-4 text-[13px] text-muted-foreground">{t.noClusters}</p>;
	return (
		<ol className="m-0 grid list-none gap-0 p-0" data-testid="gap-clusters">
			{clusters.map((c, i) => {
				const hint = fill(t.addContentHint, { keywords: c.keywords.join(", ") || "—" });
				const hintId = `gap-hint-${c.topic_id}-${i}`;
				const key = `${c.topic_id}:${c.keywords.join(",")}:${c.query_count}`;
				return (
					<li key={key} className="grid gap-2.5 border-b border-border px-4 py-3.5 last:border-b-0">
						<div className="flex flex-wrap items-center justify-between gap-2">
							<h3 className="text-[14px] font-bold">
								{fill(t.cluster, { n: i + 1 })} · {plural(t.asked, c.query_count)}
							</h3>
							<Link
								to="/add"
								search={{ kb: addKb }}
								aria-describedby={hintId}
								className={buttonVariants({ size: "sm" })}
							>
								<Upload aria-hidden />
								{t.addContent}
							</Link>
						</div>
						{c.keywords.length ? (
							<ul className="m-0 flex list-none flex-wrap gap-1.5 p-0" aria-label={hint}>
								{c.keywords.map((k) => (
									<li key={k}>
										<Badge variant="info">{k}</Badge>
									</li>
								))}
							</ul>
						) : null}
						<p id={hintId} className="sr-only">
							{hint}
						</p>
						{c.representative_queries.length ? (
							<div className="grid gap-1">
								<span className="font-mono text-[10.5px] tracking-[.08em] text-muted-foreground uppercase">
									{t.examples}
								</span>
								<ul className="m-0 grid gap-1 pl-4 text-[13px]">
									{c.representative_queries.map((qq) => (
										<li key={qq}>“{qq}”</li>
									))}
								</ul>
							</div>
						) : null}
					</li>
				);
			})}
		</ol>
	);
}

/** One report (`GET /analytics/gaps/{id}`), polled every 5 s until completed or failed. */
export function GapReportView({ id, fallbackKb, onBack }: { id: string; fallbackKb: string; onBack: () => void }) {
	const q = useQuery(gapReportQuery(id));
	const kbName = useKbNames();
	const back = (
		<Button type="button" variant="ghost" size="sm" onClick={onBack}>
			<ArrowLeft aria-hidden />
			{t.back}
		</Button>
	);
	if (q.isPending) {
		return (
			<>
				<div>{back}</div>
				<CardSkeleton lines={5} />
			</>
		);
	}
	if (q.isError) {
		return (
			<>
				<div>{back}</div>
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			</>
		);
	}
	const r = q.data;
	const scope = r.corpus_id ? kbName(r.corpus_id) : t.allKbs;
	return (
		<>
			<div>{back}</div>
			<Panel
				title={fill(t.reportTitle, { date: formatDateTime(r.started_at) })}
				sub={fill(t.reportSub, {
					scope,
					questions: formatNumber(r.query_count),
					topics: plural(t.topics, r.cluster_count),
				})}
				aside={<StatusBadge status={r.status} />}
			>
				{!isTerminal(r.status) ? (
					<p role="status" className="m-0 p-4 text-[13px] text-muted-foreground" data-testid="gap-polling">
						{t.polling}
					</p>
				) : r.status === "failed" ? (
					<div className="p-4">
						<FormAlert>{t.failed}</FormAlert>
					</div>
				) : (
					<ClusterList clusters={asClusters(r.clusters)} addKb={r.corpus_id ?? fallbackKb} />
				)}
			</Panel>
		</>
	);
}
