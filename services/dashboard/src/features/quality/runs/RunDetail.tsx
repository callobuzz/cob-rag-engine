import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { toApiError } from "@/lib/api/errors";
import { formatCost } from "@/lib/format/cost";
import { formatDateTime } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import { kbEvalRunQuery, type RunDetail as RunDetailData } from "../evals-api";
import { MetricCards } from "./MetricCards";
import { SamplesTable } from "./SamplesTable";
import { TagBreakdown } from "./TagBreakdown";

function snapshotLine(settings: RunDetailData["settings"], target: string | null): string {
	const s = (settings ?? {}) as {
		assistant?: unknown;
		retrieval?: { preset?: string };
		answer_model?: { model?: string };
	};
	const parts: string[] = [];
	const a = s.assistant ?? target;
	if (a != null) parts.push(typeof a === "number" ? `v${a}` : String(a));
	if (s.retrieval?.preset) parts.push(`${s.retrieval.preset} search`);
	if (s.answer_model?.model) parts.push(s.answer_model.model);
	return parts.join(" · ");
}

/** One run: header, warnings, a card per score, the tag table and every question. */
export function RunDetail({ kb, id, onBack }: { kb: string; id: string; onBack: () => void }) {
	const q = useQuery(kbEvalRunQuery(kb, id));
	const back = (
		<Button type="button" onClick={onBack}>
			<ArrowLeft aria-hidden />
			{t.detail.back}
		</Button>
	);
	if (q.isError) {
		const missing = toApiError(q.error).code === "eval_run_not_found";
		return (
			<div className="grid gap-4">
				{back}
				<ErrorState error={q.error} title={missing ? t.detail.notFound : undefined} onRetry={() => void q.refetch()} />
			</div>
		);
	}
	if (q.isPending) return <CardSkeleton lines={5} />;
	const run = q.data;
	const title = run.name ?? (run.kind === "traffic" ? t.runs.pastTraffic : t.runs.goldenSet);
	const running = run.status === "queued" || run.status === "running";
	const rows: [string, string][] = [
		[
			t.detail.mode,
			run.mode ? (t.runs.modes[run.mode as keyof typeof t.runs.modes] ?? run.mode) : t.detail.kind[run.kind],
		],
		[t.detail.target, snapshotLine(run.settings, run.target)],
		[t.detail.judge, run.judge_model ?? "—"],
		[
			t.detail.cost,
			run.estimated_cost_usd != null && run.mode === "full"
				? fill(t.detail.costLine, {
						actual: formatCost(run.actual_cost_usd, run.actual_cost_known),
						estimate: formatCost(run.estimated_cost_usd),
					})
				: formatCost(run.actual_cost_usd, run.actual_cost_known),
		],
		[t.detail.by, run.created_by ?? "—"],
		["", formatDateTime(run.started_at)],
	];
	return (
		<div className="grid gap-4">
			{back}
			<Panel
				title={title}
				sub={fill(t.runs.resultLine, {
					passed: Number(run.totals?.passed ?? 0),
					total: Number(run.totals?.questions ?? run.sample_size),
				})}
				aside={<StatusBadge status={run.status} label={t.runs.statuses[run.status as keyof typeof t.runs.statuses]} />}
			>
				<dl className="m-0 grid gap-x-6 gap-y-1 p-4 text-[13.5px] min-[861px]:grid-cols-2">
					{rows
						.filter(([, v]) => v)
						.map(([k, v]) => (
							<div key={`${k}-${v}`} className="flex gap-2">
								{k ? <dt className="text-muted-foreground">{k}</dt> : null}
								<dd className="m-0 font-medium">{v}</dd>
							</div>
						))}
				</dl>
			</Panel>
			{running ? (
				<p role="status" data-testid="run-polling" className="m-0 text-foreground-2">
					{t.detail.polling}
				</p>
			) : null}
			{run.error_message ? (
				<p role="alert" className="m-0 text-destructive">
					{run.error_message}
				</p>
			) : null}
			{run.warnings.map((w) => (
				<p key={w} role="note" className="m-0 rounded bg-warning-soft p-2 text-warning">
					{w}
				</p>
			))}
			<MetricCards run={run} />
			<TagBreakdown run={run} />
			<SamplesTable kb={kb} run={run} />
		</div>
	);
}
