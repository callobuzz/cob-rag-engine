import { useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleX } from "lucide-react";
import { FormAlert } from "@/components/auth/FormAlert";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { isTerminal } from "@/features/insights/api";
import { SimpleTable } from "@/features/insights/charts";
import { useKbNames } from "@/features/insights/ui";
import { Panel } from "@/features/kbs/ui";
import { isApiError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { qualityStrings } from "@/lib/strings/insights";
import { evalRunQuery } from "./api";
import { metricRows } from "./metrics";
import { BackButton } from "./runs";

const t = qualityStrings.evalDetail;

export function PassBadge({ passed }: { passed: boolean | null }) {
	if (passed == null) return <Badge>—</Badge>;
	return passed ? (
		<Badge variant="ok">
			<CircleCheck aria-hidden />
			{t.passed}
		</Badge>
	) : (
		<Badge variant="bad">
			<CircleX aria-hidden />
			{t.below}
		</Badge>
	);
}

/** One evaluation run, polled every 5 s until completed or failed; RAGAS names in plain words. */
export function EvalRunDetail({ id, onBack }: { id: string; onBack: () => void }) {
	const q = useQuery(evalRunQuery(id));
	const kbName = useKbNames();
	const back = <BackButton label={t.back} onClick={onBack} />;
	if (q.isPending)
		return (
			<>
				{back}
				<CardSkeleton lines={5} />
			</>
		);
	if (q.isError) {
		return (
			<>
				{back}
				{isApiError(q.error) && q.error.status === 404 ? (
					<FormAlert tone="warn">{t.notFound}</FormAlert>
				) : (
					<ErrorState error={q.error} onRetry={() => void q.refetch()} />
				)}
			</>
		);
	}
	const r = q.data;
	const rows = metricRows(r.metrics ?? {});
	return (
		<>
			{back}
			<Panel title={fill(t.title, { date: formatDateTime(r.created_at) })} aside={<StatusBadge status={r.status} />}>
				<KeyValue
					rows={[
						{ label: t.kb, value: r.corpus_id ? kbName(r.corpus_id) : qualityStrings.evals.anyKb },
						{
							label: t.sample,
							value: r.sample_size == null ? qualityStrings.evals.sampleAll : formatNumber(r.sample_size),
						},
						{ label: t.started, value: formatDateTime(r.created_at) },
						{ label: t.finished, value: r.completed_at ? formatDateTime(r.completed_at) : "—" },
					]}
				/>
				{!isTerminal(r.status) ? (
					<p
						role="status"
						data-testid="run-polling"
						className="m-0 border-t border-border p-4 text-[13px] text-muted-foreground"
					>
						{t.polling}
					</p>
				) : null}
				{r.error_message ? (
					<div className="border-t border-border p-4">
						<FormAlert>
							<b>{t.error}:</b> {r.error_message}
						</FormAlert>
					</div>
				) : null}
			</Panel>
			{isTerminal(r.status) ? (
				<Panel title={t.metrics}>
					{rows.length ? (
						<div className="overflow-x-auto px-2">
							<SimpleTable
								caption={t.metrics}
								headers={[t.colMetric, t.colMeaning, t.colScore, t.colTarget, t.colResult]}
								rows={rows.map((m) => ({
									key: m.key,
									cells: [
										<b key="l">{m.label}</b>,
										<span key="m" className="block text-left text-foreground-2">
											{m.meaning ?? "—"}
										</span>,
										m.score == null ? "—" : m.score.toFixed(2),
										m.threshold == null ? "—" : m.threshold.toFixed(2),
										<PassBadge key="p" passed={m.passed} />,
									],
								}))}
							/>
						</div>
					) : (
						<p className="m-0 p-4 text-[13px] text-muted-foreground">{t.noMetrics}</p>
					)}
				</Panel>
			) : null}
			<details className="rounded-lg border border-border bg-card">
				<summary className="cursor-pointer px-4 py-3 text-[14px] font-bold">{t.config}</summary>
				<pre className="m-0 overflow-x-auto border-t border-border p-4 font-mono text-[12px]">
					{JSON.stringify(r.config_snapshot, null, 2)}
				</pre>
			</details>
		</>
	);
}
