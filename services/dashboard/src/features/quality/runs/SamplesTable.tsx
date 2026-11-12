import { useQuery } from "@tanstack/react-query";
import { Fragment, useId, useState } from "react";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Switch } from "@/components/ui/switch";
import { useErrorText } from "@/features/insights/ui";
import { formatCost } from "@/lib/format/cost";
import { Markdown } from "@/lib/markdown";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import { type RunDetail, type Sample, type SampleFilters, samplesQuery, useRateSample } from "../evals-api";
import { metricLabel, pct } from "./format";

const RATINGS = ["good", "acceptable", "poor"] as const;

interface Check {
	name: string;
	status: string;
	detail: string;
}

function failedChecks(s: Sample): Check[] {
	const checks = s.behaviour && Array.isArray(s.behaviour.checks) ? (s.behaviour.checks as Check[]) : [];
	return checks.filter((c) => c.status === "fail");
}

function SampleDetail({ s }: { s: Sample }) {
	const ctxId = useId();
	const [shown, setShown] = useState<number | null>(null);
	const reasons = Object.entries(s.reasons);
	const fails = failedChecks(s);
	return (
		<div className="grid gap-3 p-3">
			{s.answer ? (
				<div>
					<h4 className="m-0 text-[13px] font-semibold">{t.samples.answer}</h4>
					<Markdown
						text={s.answer}
						sources={s.contexts.map((_, i) => i + 1)}
						onCite={(n) => {
							setShown(n - 1);
							document.getElementById(`${ctxId}-${n - 1}`)?.scrollIntoView?.({ block: "nearest" });
						}}
					/>
				</div>
			) : null}
			{s.error ? <p className="m-0 text-destructive">{s.error}</p> : null}
			{reasons.length || fails.length ? (
				<div>
					<h4 className="m-0 text-[13px] font-semibold">{t.samples.reasons}</h4>
					<ul className="m-0 grid gap-1 pl-5">
						{reasons.map(([k, v]) => (
							<li key={k}>
								{k === "summary" ? "" : `${metricLabel(k)}: `}
								{v}
							</li>
						))}
						{fails.map((c) => (
							<li key={c.name}>{c.detail}</li>
						))}
					</ul>
				</div>
			) : null}
			<div>
				<h4 className="m-0 text-[13px] font-semibold">{t.samples.contexts}</h4>
				{s.contexts.length === 0 ? (
					<p className="m-0 text-foreground-2">{t.samples.noContexts}</p>
				) : (
					<ol className="m-0 grid gap-1 pl-5">
						{s.contexts.map((c, i) => (
							<li
								// biome-ignore lint/suspicious/noArrayIndexKey: passages have no id; the list is fixed per sample
								key={i}
								id={`${ctxId}-${i}`}
								className={shown === i ? "rounded bg-primary-soft px-1" : undefined}
							>
								{String((c as { text?: unknown }).text ?? "")}
							</li>
						))}
					</ol>
				)}
			</div>
			{s.cost_usd != null ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">
					{t.samples.cost}: {formatCost(s.cost_usd)}
				</p>
			) : null}
		</div>
	);
}

/** The questions of a run: filters, per-question scores, details on demand, a rating. */
export function SamplesTable({ kb, run }: { kb: string; run: RunDetail }) {
	const [filters, setFilters] = useState<SampleFilters>({});
	const [open, setOpen] = useState<string | null>(null);
	const q = useQuery(samplesQuery(kb, run.id, filters));
	const rate = useRateSample(kb);
	const errorText = useErrorText();
	const ids = { failed: useId(), metric: useId(), below: useId(), tag: useId() };
	const tags = run.tags.map((g) => `${g.tag}:${g.value}`);
	const set = (patch: Partial<SampleFilters>) => setFilters((f) => ({ ...f, ...patch }));
	return (
		<section aria-label={t.detail.questions} className="grid gap-3">
			<div className="flex flex-wrap items-end gap-3">
				<div className="flex items-center gap-2">
					<Switch
						id={ids.failed}
						checked={!!filters.failed_only}
						onCheckedChange={(v) => set({ failed_only: v || undefined })}
					/>
					<label htmlFor={ids.failed}>{t.samples.failedOnly}</label>
				</div>
				<div className="grid gap-1">
					<label htmlFor={ids.metric} className="text-[13px] font-semibold">
						{t.samples.metric}
					</label>
					<NativeSelect
						id={ids.metric}
						value={filters.metric ?? ""}
						onChange={(e) => set({ metric: e.target.value || undefined })}
					>
						<option value="">{t.samples.all}</option>
						{run.metrics.map((m) => (
							<option key={m.metric} value={m.metric}>
								{metricLabel(m.metric)}
							</option>
						))}
					</NativeSelect>
				</div>
				<div className="grid gap-1">
					<label htmlFor={ids.below} className="text-[13px] font-semibold">
						{t.samples.below}
					</label>
					<input
						id={ids.below}
						type="number"
						min={0}
						max={1}
						step={0.05}
						className="h-[38px] w-24 rounded-sm border border-input bg-muted px-2"
						value={filters.below ?? ""}
						onChange={(e) => set({ below: e.target.value === "" ? undefined : Number(e.target.value) })}
					/>
				</div>
				{tags.length ? (
					<div className="grid gap-1">
						<label htmlFor={ids.tag} className="text-[13px] font-semibold">
							{t.samples.tag}
						</label>
						<NativeSelect
							id={ids.tag}
							value={filters.tag ?? ""}
							onChange={(e) => set({ tag: e.target.value || undefined })}
						>
							<option value="">{t.samples.all}</option>
							{tags.map((tag) => (
								<option key={tag} value={tag}>
									{tag}
								</option>
							))}
						</NativeSelect>
					</div>
				) : null}
			</div>
			{q.isError ? (
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			) : q.isPending ? (
				<TableSkeleton rows={3} />
			) : q.data.items.length === 0 ? (
				<p className="m-0 text-foreground-2">{t.samples.empty}</p>
			) : (
				<div className="overflow-x-auto">
					<table className="w-full border-collapse text-[13.5px]">
						<caption className="sr-only">{t.detail.questions}</caption>
						<thead>
							<tr className="text-left text-foreground-2">
								<th scope="col" className="px-2 py-1.5">
									{t.samples.colResult}
								</th>
								<th scope="col" className="px-2 py-1.5">
									{t.samples.colQuestion}
								</th>
								<th scope="col" className="px-2 py-1.5">
									{t.samples.colScores}
								</th>
								<th scope="col" className="px-2 py-1.5">
									{t.samples.colRating}
								</th>
							</tr>
						</thead>
						<tbody>
							{q.data.items.map((s) => {
								const expanded = open === s.id;
								return (
									<Fragment key={s.id}>
										<tr className="border-t border-border align-top">
											<td className="px-2 py-1.5 whitespace-nowrap">
												{s.passed == null ? t.samples.unscored : s.passed ? t.samples.passed : t.samples.failed}
											</td>
											<td className="px-2 py-1.5">
												{s.question}
												<button
													type="button"
													className="ml-2 underline underline-offset-4"
													aria-expanded={expanded}
													aria-controls={`sample-${s.id}`}
													onClick={() => setOpen(expanded ? null : s.id)}
												>
													{expanded ? t.samples.hide : t.samples.details}
												</button>
											</td>
											<td className="px-2 py-1.5">
												{Object.entries(s.scores)
													.filter(([, v]) => v != null)
													.map(([k, v]) => `${metricLabel(k)} ${pct(v)}`)
													.join(" · ")}
											</td>
											<td className="px-2 py-1.5">
												<fieldset aria-label={t.samples.colRating} className="m-0 flex flex-wrap gap-1 border-0 p-0">
													{RATINGS.map((r) => (
														<button
															key={r}
															type="button"
															aria-pressed={s.rating === r}
															className="rounded border border-border px-2 py-0.5 aria-pressed:bg-primary-soft aria-pressed:font-semibold"
															onClick={() =>
																rate.mutate(
																	{ id: s.id, rating: s.rating === r ? null : r },
																	{ onError: (e) => toast.error(errorText(e)) },
																)
															}
														>
															{t.samples.ratings[r]}
														</button>
													))}
												</fieldset>
											</td>
										</tr>
										{expanded ? (
											<tr id={`sample-${s.id}`}>
												<td colSpan={4} className="bg-muted">
													<SampleDetail s={s} />
												</td>
											</tr>
										) : null}
									</Fragment>
								);
							})}
						</tbody>
					</table>
				</div>
			)}
		</section>
	);
}
