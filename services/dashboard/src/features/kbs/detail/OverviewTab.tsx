import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Bot, FileText, MessageSquareCode } from "lucide-react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { buttonVariants } from "@/components/ui/button";
import { formatDate, formatRelative } from "@/lib/format/date";
import { formatNumber, formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { type CorpusDetail, trendsQuery, usageQuery, utcDay } from "../api";
import { Kpi, Panel } from "../ui";

/** Questions this week vs the week before, from `group_by=day` usage over 14 days. */
export function weekOverWeek(rows: { key: string | null; requests: number }[], today: string = utcDay(0)) {
	const cut = new Date(`${today}T00:00:00Z`).getTime() - 6 * 86_400_000;
	let now = 0;
	let prev = 0;
	for (const r of rows) {
		if (!r.key) continue;
		const at = new Date(`${r.key.slice(0, 10)}T00:00:00Z`).getTime();
		if (at >= cut) now += r.requests;
		else prev += r.requests;
	}
	return { now, prev, delta: prev > 0 ? (now - prev) / prev : null };
}

/** Count-weighted mean of the daily `avg_confidence` values. */
export function averageConfidence(points: { count: number; avg_confidence?: number | null }[]): number | null {
	let n = 0;
	let sum = 0;
	for (const p of points) {
		if (p.avg_confidence == null || !p.count) continue;
		n += p.count;
		sum += p.avg_confidence * p.count;
	}
	return n ? sum / n : null;
}

/** Overview: documents, questions · 7 d (with delta), answer confidence, last activity, links. */
export function OverviewTab({ kb }: { kb: CorpusDetail }) {
	const usage = useQuery(usageQuery({ corpus_id: kb.id, group_by: "day", start: utcDay(13), end: utcDay(0) }));
	const trends = useQuery(trendsQuery(kb.id));
	const wow = usage.data ? weekOverWeek(usage.data.rows) : null;
	const conf = trends.data ? averageConfidence(trends.data.data) : null;

	return (
		<div className="grid gap-4">
			<div className="grid gap-4 min-[861px]:grid-cols-3">
				<Kpi
					label={t.overview.documents}
					value={formatNumber(kb.document_count)}
					delta={fill(t.overview.passages, { n: formatNumber(kb.chunk_count) })}
				/>
				{usage.isError ? (
					<ErrorState title={t.overview.questions} error={usage.error} onRetry={() => void usage.refetch()} />
				) : (
					<Kpi
						label={t.overview.questions}
						value={wow ? formatNumber(wow.now) : "…"}
						delta={
							wow ? (
								wow.delta == null ? (
									t.overview.noPrev
								) : (
									<>
										<b className={wow.delta >= 0 ? "text-success" : "text-destructive"}>
											{wow.delta >= 0 ? "▲" : "▼"} {formatPercent(Math.abs(wow.delta))}
										</b>{" "}
										{t.overview.vsPrev}
									</>
								)
							) : null
						}
					/>
				)}
				{trends.isError ? (
					<ErrorState title={t.overview.confidence} error={trends.error} onRetry={() => void trends.refetch()} />
				) : (
					<Kpi
						label={t.overview.confidence}
						value={trends.isPending ? "…" : conf == null ? "—" : formatPercent(conf)}
						delta={conf == null && !trends.isPending ? t.overview.confidenceNone : t.overview.confidenceHint}
					/>
				)}
			</div>
			<div className="grid gap-4 min-[1101px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
				<Panel title={t.detail.tabs.overview}>
					<KeyValue
						rows={[
							{
								label: t.overview.lastActivity,
								value: kb.last_activity_at ? formatRelative(kb.last_activity_at) : t.list.never,
							},
							{ label: t.overview.created, value: formatDate(kb.created_at) },
							{ label: t.create.id, value: kb.id },
							{ label: t.create.collection, value: kb.qdrant_collection },
						]}
					/>
				</Panel>
				<Panel title={t.overview.links}>
					<div className="flex flex-wrap gap-2 p-4">
						<Link to="/documents" search={{ kb: kb.id }} className={buttonVariants({ variant: "outline" })}>
							<FileText aria-hidden />
							{t.overview.linkDocuments}
						</Link>
						<Link to="/widget" search={{ kb: kb.id }} className={buttonVariants({ variant: "outline" })}>
							<MessageSquareCode aria-hidden />
							{t.overview.linkWidget}
						</Link>
						<Link to="/agents" search={{ kb: kb.id }} className={buttonVariants({ variant: "outline" })}>
							<Bot aria-hidden />
							{t.overview.linkAgents}
						</Link>
					</div>
				</Panel>
			</div>
		</div>
	);
}
