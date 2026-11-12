import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { ClipboardCopy } from "lucide-react";
import { useId, useState } from "react";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Button } from "@/components/ui/button";
import { isTerminal } from "@/features/insights/api";
import { SimpleTable } from "@/features/insights/charts";
import { useKbNames } from "@/features/insights/ui";
import { Panel } from "@/features/kbs/ui";
import { isApiError } from "@/lib/api/errors";
import { corporaSwitcherQuery } from "@/lib/api/queries";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { qualityStrings } from "@/lib/strings/insights";
import { optimizeRunQuery } from "./api";
import { metricRows } from "./metrics";
import { asParetoEntry, prefillFromParams } from "./optimize-prefill";
import { BackButton } from "./runs";

const t = qualityStrings.optimizeDetail;

const paramName = (k: string) => t.params[k] ?? k;
const value = (v: unknown) =>
	typeof v === "number" ? (Number.isInteger(v) ? formatNumber(v) : v.toFixed(2)) : JSON.stringify(v);

function ParamList({ params }: { params: Record<string, unknown> }) {
	return (
		<ul className="m-0 grid list-none gap-0.5 p-0 text-left text-[12.5px]">
			{Object.entries(params).map(([k, v]) => (
				<li key={k}>
					{paramName(k)}: <b className="tabular-nums">{value(v)}</b>
				</li>
			))}
		</ul>
	);
}

function ScoreList({ metrics }: { metrics: Record<string, unknown> }) {
	const rows = metricRows(metrics);
	return (
		<ul className="m-0 grid list-none gap-0.5 p-0 text-left text-[12.5px]">
			{rows.map((m) => (
				<li key={m.key}>
					{m.label}: <b className="tabular-nums">{m.score == null ? "—" : m.score.toFixed(2)}</b>
				</li>
			))}
		</ul>
	);
}

/**
 * One optimisation run, polled until done: the suggested (best) settings with "Copy to Retrieval
 * tab" — the knowledge base's Retrieval form opens pre-filled and unsaved (D3; G-15: nothing is
 * applied automatically) — and the Pareto configurations.
 */
export function OptimizeRunDetail({ id, fallbackKb, onBack }: { id: string; fallbackKb: string; onBack: () => void }) {
	const q = useQuery(optimizeRunQuery(id));
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
	const best = asParetoEntry(r.best_config);
	const pareto = (r.pareto_configs ?? []).map(asParetoEntry);
	const hasBest = Object.keys(best.params).length > 0;
	return (
		<>
			{back}
			<Panel title={fill(t.title, { date: formatDateTime(r.created_at) })} aside={<StatusBadge status={r.status} />}>
				<KeyValue
					rows={[
						{
							label: qualityStrings.optimize.colKb,
							value: r.corpus_id ? kbName(r.corpus_id) : qualityStrings.optimize.anyKb,
						},
						{ label: qualityStrings.optimize.colTrials, value: r.n_trials == null ? "—" : formatNumber(r.n_trials) },
						{
							label: qualityStrings.optimize.colFinished,
							value: r.completed_at ? formatDateTime(r.completed_at) : "—",
						},
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
				<>
					<Panel title={t.best} sub={t.bestSub}>
						{hasBest ? (
							<div className="grid gap-4 p-4 min-[861px]:grid-cols-2">
								<ParamList params={best.params} />
								<ScoreList metrics={best.metrics} />
								<div className="min-[861px]:col-span-2">
									<CopyToRetrieval params={best.params} runKb={r.corpus_id ?? null} fallbackKb={fallbackKb} />
								</div>
							</div>
						) : (
							<p className="m-0 p-4 text-[13px] text-muted-foreground">{t.noBest}</p>
						)}
					</Panel>
					<Panel title={t.pareto} sub={t.paretoSub}>
						{pareto.length ? (
							<div className="overflow-x-auto px-2">
								<SimpleTable
									caption={t.pareto}
									headers={[t.colTrial, t.colParams, t.colScores]}
									rows={pareto.map((p, i) => ({
										key: `${p.trial ?? i}-${i}`,
										cells: [
											p.trial == null ? "—" : `#${p.trial}`,
											<ParamList key="p" params={p.params} />,
											<ScoreList key="s" metrics={p.metrics} />,
										],
									}))}
								/>
							</div>
						) : (
							<p className="m-0 p-4 text-[13px] text-muted-foreground">{t.noPareto}</p>
						)}
					</Panel>
				</>
			) : null}
		</>
	);
}

function CopyToRetrieval({
	params,
	runKb,
	fallbackKb,
}: {
	params: Record<string, unknown>;
	runKb: string | null;
	fallbackKb: string;
}) {
	const navigate = useNavigate();
	const kbs = useQuery(corporaSwitcherQuery);
	const kbName = useKbNames();
	const [target, setTarget] = useState(runKb ?? fallbackKb);
	const id = useId();
	const { prefill, engineWide } = prefillFromParams(params);
	const items = [...(kbs.data?.items ?? [])].sort((a, b) => a.name.localeCompare(b.name, "en-IN"));
	return (
		<div className="grid gap-3 rounded-lg border border-border bg-secondary p-3">
			{runKb ? null : (
				<>
					<p className="m-0 text-[13px] text-foreground-2">{t.copyNeedsKb}</p>
					<Field id={id} label={t.copyTo}>
						<NativeSelect id={id} className="max-w-[360px]" value={target} onChange={(e) => setTarget(e.target.value)}>
							{items.map((k) => (
								<option key={k.id} value={k.id}>
									{k.tenant_name ? `${k.name} · ${k.tenant_name}` : k.name}
								</option>
							))}
						</NativeSelect>
					</Field>
				</>
			)}
			<div className="flex flex-wrap items-center gap-3">
				<Button
					type="button"
					variant="default"
					disabled={!target || Object.keys(prefill).length === 0}
					onClick={() =>
						void navigate({
							to: "/kbs/$kbId",
							params: { kbId: target },
							search: { tab: "retrieval" },
							state: (prev) => ({ ...prev, retrievalPrefill: prefill }),
						})
					}
				>
					<ClipboardCopy aria-hidden />
					{t.copy}
				</Button>
				<span className="text-[12.5px] text-muted-foreground">{fill(t.copyHint, { kb: kbName(target) })}</span>
			</div>
			{engineWide.length ? (
				<p className="m-0 text-[12.5px] text-muted-foreground" data-testid="engine-wide-params">
					{fill(t.notPerKb, { names: engineWide.map(paramName).join(", ") })}
				</p>
			) : null}
		</div>
	);
}
