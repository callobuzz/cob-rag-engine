import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { formatCost } from "@/lib/format/cost";
import { formatDateTime } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import { goldenSetsQuery, kbEvalRunsQuery, type Run } from "../evals-api";
import { runResult } from "./format";
import { NewRunDialog } from "./NewRunDialog";

const r = t.runs;

/** This knowledge base's runs, newest first, and the "New run" dialog. */
export function RunsTab({
	kb,
	initialSetId,
	onOpen,
}: {
	kb: string;
	initialSetId?: string;
	onOpen: (id: string) => void;
}) {
	const runs = useQuery(kbEvalRunsQuery(kb));
	const sets = useQuery(goldenSetsQuery(kb));
	const [creating, setCreating] = useState(false);
	const setName = new Map((sets.data?.items ?? []).map((s) => [s.id, s.name]));
	const nameOf = (x: Run) =>
		x.name ?? (x.kind === "traffic" ? r.pastTraffic : (setName.get(x.set_id ?? "") ?? r.goldenSet));

	const columns: Column<Run>[] = [
		{
			id: "name",
			header: r.colName,
			rowHeader: true,
			cell: (x) => (
				<button
					type="button"
					className="text-left font-semibold underline-offset-4 hover:underline"
					aria-label={fill(r.open, { name: nameOf(x) })}
					onClick={() => onOpen(x.id)}
				>
					{nameOf(x)}
				</button>
			),
		},
		{
			id: "mode",
			header: r.colMode,
			stackLabel: true,
			cell: (x) =>
				x.kind === "traffic"
					? r.pastTraffic
					: `${r.modes[x.mode as keyof typeof r.modes] ?? x.mode ?? ""}${
							r.trigger[x.triggered_by as keyof typeof r.trigger]
								? ` · ${r.trigger[x.triggered_by as keyof typeof r.trigger]}`
								: ""
						}`,
		},
		{
			id: "result",
			header: r.colResult,
			stackLabel: true,
			cell: (x) => {
				const res = runResult(x.totals);
				return res ? fill(r.resultLine, res) : "—";
			},
		},
		{
			id: "cost",
			header: r.colCost,
			align: "right",
			stackLabel: true,
			cell: (x) => (x.actual_cost_usd == null ? "—" : formatCost(x.actual_cost_usd, x.actual_cost_known)),
		},
		{
			id: "status",
			header: r.colStatus,
			stackLabel: true,
			cell: (x) => <StatusBadge status={x.status} label={r.statuses[x.status as keyof typeof r.statuses]} />,
		},
		{
			id: "when",
			header: r.colWhen,
			stackLabel: true,
			className: "whitespace-nowrap",
			cell: (x) => formatDateTime(x.started_at),
		},
	];

	const canRun = (sets.data?.items.length ?? 0) > 0;
	const aside = (
		<Button type="button" variant="default" disabled={!canRun} onClick={() => setCreating(true)}>
			<FlaskConical aria-hidden />
			{t.newRun.title}
		</Button>
	);

	return (
		<>
			<Panel title={r.title} sub={canRun || sets.isPending ? r.sub : r.noSets} aside={aside}>
				{runs.isError ? (
					<div className="p-4">
						<ErrorState error={runs.error} title={r.error} onRetry={() => void runs.refetch()} />
					</div>
				) : runs.isPending ? (
					<div className="p-4">
						<TableSkeleton rows={3} />
					</div>
				) : runs.data.items.length === 0 ? (
					<div className="p-4">
						<EmptyState title={r.empty} body={r.emptyBody} />
					</div>
				) : (
					<div className="px-2">
						<DataTable columns={columns} rows={runs.data.items} rowKey={(x) => x.id} caption={r.title} />
					</div>
				)}
			</Panel>
			{creating && sets.data ? (
				<NewRunDialog
					kb={kb}
					sets={sets.data.items}
					initialSetId={initialSetId}
					onStarted={onOpen}
					onClose={() => setCreating(false)}
				/>
			) : null}
		</>
	);
}
