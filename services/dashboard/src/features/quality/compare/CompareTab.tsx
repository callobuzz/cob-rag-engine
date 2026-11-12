import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Panel } from "@/features/kbs/ui";
import { fill } from "@/lib/strings/en";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import { compareQuery, kbEvalRunsQuery } from "../evals-api";
import { metricLabel, pct } from "../runs/format";

const c = t.compare;
type Show = "all" | "improved" | "regressed";

/**
 * Two to four finished runs of one golden set side by side: per-metric scores with their 95 %
 * intervals, the difference from the baseline (the first run picked) marked "not significant"
 * when the interval crosses zero, and per-question changes.
 */
export function CompareTab({ kb, ids, onIds }: { kb: string; ids: string[]; onIds: (ids: string[]) => void }) {
	const runs = useQuery(kbEvalRunsQuery(kb, { kind: "golden" }));
	const cmp = useQuery(compareQuery(kb, ids));
	const [show, setShow] = useState<Show>("all");
	const listId = useId();
	const toggle = (id: string) => onIds(ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id].slice(0, 4));
	const done = (runs.data?.items ?? []).filter((x) => x.status === "completed");
	return (
		<div className="grid gap-4">
			<Panel title={c.title} sub={c.pick}>
				<fieldset className="m-0 grid gap-1 border-0 p-4" aria-labelledby={listId}>
					<legend id={listId} className="sr-only">
						{c.pickLabel}
					</legend>
					{runs.isError ? (
						<ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
					) : done.length === 0 && !runs.isPending ? (
						<p className="m-0 text-foreground-2">{c.noRuns}</p>
					) : (
						done.map((r) => (
							<label key={r.id} className="flex items-center gap-2">
								<input type="checkbox" checked={ids.includes(r.id)} onChange={() => toggle(r.id)} />
								{r.name ?? c.unnamed}
								{r.mode ? ` · ${r.mode}` : ""}
							</label>
						))
					)}
				</fieldset>
			</Panel>
			{cmp.isError ? <ErrorState error={cmp.error} onRetry={() => void cmp.refetch()} /> : null}
			{cmp.data ? (
				<>
					{cmp.data.warnings.map((w) => (
						<p key={w} role="note" className="m-0 rounded bg-warning-soft p-2 text-warning">
							{w}
						</p>
					))}
					<div className="overflow-x-auto">
						<table className="w-full border-collapse text-[13.5px]">
							<caption className="sr-only">{c.metric}</caption>
							<thead>
								<tr className="text-left text-foreground-2">
									<th scope="col" className="px-2 py-1.5">
										{c.metric}
									</th>
									{cmp.data.runs.map((r, i) => (
										<th key={r.id} scope="col" className="px-2 py-1.5">
											{r.name ?? c.unnamed}
											{i === 0 ? ` (${c.baseline})` : ""}
										</th>
									))}
								</tr>
							</thead>
							<tbody>
								{cmp.data.metrics.map((m) => (
									<tr key={m.metric} className="border-t border-border align-top">
										<th scope="row" className="px-2 py-1.5 text-left font-medium">
											{metricLabel(m.metric)}
										</th>
										{m.per_run.map((s, i) => {
											const d = m.deltas[i];
											return (
												// biome-ignore lint/suspicious/noArrayIndexKey: columns are the picked runs, in order
												<td key={`${m.metric}-${i}`} className="px-2 py-1.5">
													{pct(s.mean)} ({pct(s.ci_low)}–{pct(s.ci_high)})
													{d?.mean != null ? (
														<>
															{" "}
															<strong>
																{fill(c.pts, { sign: d.mean >= 0 ? "+" : "−", n: Math.round(Math.abs(d.mean) * 100) })}
															</strong>
															{!d.significant ? (
																<span className="ml-1 rounded border border-border px-1 text-[12px]">
																	{c.notSignificant}
																</span>
															) : null}
														</>
													) : null}
												</td>
											);
										})}
									</tr>
								))}
							</tbody>
						</table>
					</div>
					<fieldset aria-label={c.questionsLabel} className="m-0 flex gap-2 border-0 p-0">
						{(["all", "improved", "regressed"] as const).map((k) => (
							<button
								key={k}
								type="button"
								aria-pressed={show === k}
								className="rounded border border-border px-2 py-0.5 aria-pressed:bg-primary-soft aria-pressed:font-semibold"
								onClick={() => setShow(k)}
							>
								{k === "all" ? c.all : c.changes[k]}
							</button>
						))}
					</fieldset>
					<div className="overflow-x-auto">
						<table className="w-full border-collapse text-[13.5px]">
							<caption className="sr-only">{c.colQuestion}</caption>
							<tbody>
								{cmp.data.questions
									.filter((q) => show === "all" || q.change.slice(1).includes(show))
									.map((q) => (
										<tr key={q.golden_pair_id} className="border-t border-border">
											<th scope="row" className="px-2 py-1.5 text-left font-normal">
												{q.question}
											</th>
											{q.change.slice(1).map((ch, i) => (
												// biome-ignore lint/suspicious/noArrayIndexKey: one cell per compared run, in order
												<td key={`${q.golden_pair_id}-${i}`} className="px-2 py-1.5">
													{c.changes[ch]}
												</td>
											))}
										</tr>
									))}
							</tbody>
						</table>
					</div>
				</>
			) : null}
		</div>
	);
}
