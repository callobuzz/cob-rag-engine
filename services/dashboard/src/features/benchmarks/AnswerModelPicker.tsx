import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useCurrency } from "@/lib/format/currency";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";
import { type AnswerModelResult, answerBenchmarksQuery, friendlyName, ms, pct0, perThousand } from "./api";
import { TagBadges } from "./parts";

const COLLAPSED = 5;

/**
 * Benchmarked answer models beside the main-model field: pass rate, how often it declines when the
 * sources do not say, first-word time and cost, with the engine's badges. Choosing a row hands the
 * model to the form. Renders nothing when the engine has no results.
 */
export function AnswerModelPicker({
	model,
	readOnly,
	onPick,
}: {
	model: string;
	readOnly: boolean;
	onPick: (m: AnswerModelResult) => void;
}) {
	const q = useQuery(answerBenchmarksQuery);
	const currency = useCurrency();
	const [all, setAll] = useState(false);
	if (q.isPending) return null;
	if (q.isError) return <ErrorState error={q.error} title={t.answerPicker.error} onRetry={() => void q.refetch()} />;
	const rows = q.data.benchmarks?.models ?? [];
	if (!q.data.available || rows.length === 0) return null;
	const shown = all ? rows : rows.filter((m) => m.passes).slice(0, COLLAPSED);
	return (
		<section aria-label={t.answerPicker.title} className="grid gap-2 rounded-md border border-border bg-muted p-3">
			<div className="flex flex-wrap items-baseline justify-between gap-2">
				<h3 className="m-0 text-[13px] font-semibold">{t.answerPicker.title}</h3>
				<Link to="/benchmarks" className="text-[12.5px] text-primary hover:underline">
					{t.answerPicker.more}
				</Link>
			</div>
			<p className="m-0 text-[12.5px] text-muted-foreground">{t.answerPicker.lead}</p>
			<ul aria-label={t.answerPicker.listLabel} className="m-0 grid list-none gap-2 p-0">
				{shown.map((m) => {
					const selected = m.model === model.trim();
					const hindi = m.by_language.hindi_all;
					return (
						<li
							key={m.key}
							data-selected={selected || undefined}
							className={`grid gap-1.5 rounded-md border border-border bg-card p-2.5`}
						>
							<div className="flex flex-wrap items-center gap-1.5">
								<span className="grid">
									<span className="text-[13px] font-bold">{friendlyName(m.model)}</span>
									<code className="font-mono text-[11.5px] break-all text-muted-foreground">{m.model}</code>
								</span>
								<TagBadges tags={m.tags} selfHostable={m.self_hostable} />
								{selected ? <Badge variant="outline">{t.answerPicker.selected}</Badge> : null}
								<span className="ml-auto" />
								<Button
									type="button"
									size="sm"
									disabled={readOnly || selected}
									aria-label={fill(t.answerPicker.useLabel, { model: m.model })}
									onClick={() => onPick(m)}
								>
									{t.answerPicker.use}
								</Button>
							</div>
							<div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[12.5px] text-foreground-2 tabular-nums">
								<span>{fill(t.answerPicker.pass, { v: pct0(m.passed) })}</span>
								<span>{fill(t.answerPicker.noAnswer, { v: pct0(m.refusal_correct_unanswerable) })}</span>
								<span>{fill(t.answerPicker.ttft, { v: ms(m.ttft_p50_ms) })}</span>
								<span>{fill(t.answerPicker.cost, { v: perThousand(m.usd_per_1000_answers, currency) })}</span>
								<span>{fill(t.answerPicker.hindi, { v: pct0(hindi?.passed) })}</span>
							</div>
							{m.passes ? null : (
								<p role="note" className="m-0 flex flex-wrap items-center gap-1.5 text-[12.5px] text-warning">
									<Badge variant="warn">{t.answer.below}</Badge>
									<span>
										{fill(t.answer.whyLine, { reasons: m.fails.map((f) => t.answer.why[f] ?? f).join(", ") })}
									</span>
								</p>
							)}
						</li>
					);
				})}
			</ul>
			{rows.length > COLLAPSED || rows.some((m) => !m.passes) ? (
				<Button
					type="button"
					variant="ghost"
					size="sm"
					className="justify-self-start"
					onClick={() => setAll((v) => !v)}
				>
					{all ? t.answerPicker.showLess : fill(t.answerPicker.showAll, { n: rows.length })}
				</Button>
			) : null}
		</section>
	);
}
