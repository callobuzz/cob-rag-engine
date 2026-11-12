import { useQuery } from "@tanstack/react-query";
import { Radar } from "lucide-react";
import { toast } from "sonner";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Pager } from "@/components/patterns/Pager";
import { CardSkeleton, TableSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { isApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import { GAPS_PAGE_SIZE, type GapReport, gapListQuery, latestGapQuery, useRunGaps } from "./api";
import { asClusters, ClusterList, GapReportView } from "./GapReport";
import type { ScopeProps } from "./InsightsPage";
import { Note, useErrorText, useKbNames } from "./ui";

const t = insightsStrings.gaps;

/**
 * Knowledge gaps: the report list (`GET /analytics/gaps`), "Run gap detection" (one knowledge
 * base → `POST /analytics/gaps/run`, owners and members alike; the super admin with "All" →
 * `POST /admin/gaps/trigger`, the global report), a report view with polling, and — for the super
 * admin — the latest engine-wide report (`GET /admin/gaps`).
 */
export function GapsTab({ kb, corpusId, search, setSearch }: ScopeProps) {
	const principal = usePrincipal();
	const isAdmin = principal?.view === "admin";
	const run = useRunGaps();
	const errorText = useErrorText();
	const global = !corpusId && isAdmin;
	const canRun = !!corpusId || global;

	const onRun = () =>
		run.mutate(
			{ corpusId, global },
			{
				onSuccess: (id) => {
					toast.success(t.queued, { description: t.queuedBody });
					setSearch({ report: id });
				},
				onError: (e) => toast.error(errorText(e)),
			},
		);

	const runButton = (
		<Button type="button" variant="default" disabled={!canRun || run.isPending} onClick={onRun}>
			<Radar aria-hidden />
			{run.isPending ? t.running : global ? t.runAll : t.run}
		</Button>
	);

	if (search.report) {
		return <GapReportView id={search.report} fallbackKb={kb.id} onBack={() => setSearch({ report: undefined })} />;
	}

	return (
		<>
			<ReportList
				corpusId={corpusId}
				page={search.page ?? 1}
				onPage={(p) => setSearch({ page: p })}
				onOpen={(id) => setSearch({ report: id })}
				aside={runButton}
				note={canRun ? null : t.needsKb}
			/>
			{isAdmin ? <LatestReport fallbackKb={kb.id} /> : null}
		</>
	);
}

function ReportList({
	corpusId,
	page,
	onPage,
	onOpen,
	aside,
	note,
}: {
	corpusId: string | undefined;
	page: number;
	onPage: (p: number) => void;
	onOpen: (id: string) => void;
	aside: React.ReactNode;
	note: string | null;
}) {
	const q = useQuery(gapListQuery({ corpusId, page }));
	const kbName = useKbNames();
	const columns: Column<GapReport>[] = [
		{
			id: "started",
			header: t.colStarted,
			rowHeader: true,
			cell: (r) => (
				<button
					type="button"
					className="text-left font-semibold underline-offset-4 hover:underline"
					aria-label={fill(t.viewReport, { date: formatDateTime(r.started_at) })}
					onClick={() => onOpen(r.id)}
				>
					{formatDateTime(r.started_at)}
				</button>
			),
		},
		{ id: "scope", header: t.colScope, stackLabel: true, cell: (r) => (r.corpus_id ? kbName(r.corpus_id) : t.allKbs) },
		{ id: "status", header: t.colStatus, stackLabel: true, cell: (r) => <StatusBadge status={r.status} /> },
		{
			id: "questions",
			header: t.colQuestions,
			align: "right",
			stackLabel: true,
			cell: (r) => formatNumber(r.query_count),
		},
		{
			id: "topics",
			header: t.colTopics,
			align: "right",
			stackLabel: true,
			cell: (r) => formatNumber(r.cluster_count),
		},
	];
	return (
		<Panel title={t.title} sub={t.sub} aside={aside}>
			{note ? <Note testId="gaps-needs-kb">{note}</Note> : null}
			{q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
				</div>
			) : q.isPending ? (
				<div className="p-4">
					<TableSkeleton rows={3} />
				</div>
			) : (
				<>
					<div className="px-2">
						<DataTable columns={columns} rows={q.data.items} rowKey={(r) => r.id} caption={t.title} empty={t.empty} />
					</div>
					{q.data.total > GAPS_PAGE_SIZE ? (
						<Pager page={page} pageSize={GAPS_PAGE_SIZE} total={q.data.total} onPage={onPage} />
					) : null}
				</>
			)}
		</Panel>
	);
}

function LatestReport({ fallbackKb }: { fallbackKb: string }) {
	const q = useQuery(latestGapQuery);
	const kbName = useKbNames();
	const none = q.isError && isApiError(q.error) && q.error.status === 404;
	const r = q.data;
	return (
		<Panel
			title={t.latest}
			sub={r ? `${r.corpus_id ? kbName(r.corpus_id) : t.allKbs} · ${formatDateTime(r.created_at)}` : t.latestSub}
			aside={r ? <StatusBadge status={r.status} /> : undefined}
		>
			{none ? (
				<Note>{t.latestNone}</Note>
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.latestError} onRetry={() => void q.refetch()} />
				</div>
			) : !r ? (
				<div className="p-4">
					<CardSkeleton lines={3} />
				</div>
			) : (
				<ClusterList clusters={asClusters(r.topics)} addKb={r.corpus_id ?? fallbackKb} />
			)}
		</Panel>
	);
}
