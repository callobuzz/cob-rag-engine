import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useCurrency } from "@/lib/format/currency";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";
import { type FastModelResult, fastBenchmarksQuery, fastTags, pct, perThousand, sameHosts, seconds } from "./api";
import { BudgetCallout, budgetOffer } from "./BudgetCallout";
import { BelowBar, TagBadges, ThinkingNote } from "./parts";

/**
 * Benchmarked fast models for a knowledge base's model tab: one row per measured model with the
 * numbers that matter (accuracy, p90 label speed, cost per 1,000 messages). Choosing a row hands
 * the model and its pinned hosts to the form. Renders nothing when the engine has no results.
 */
export function FastModelPicker({
	model,
	hosts,
	readOnly,
	onPick,
	onSwitchBudget,
}: {
	model: string;
	hosts: readonly string[];
	readOnly: boolean;
	onPick: (m: FastModelResult) => void;
	/** Saves the budget model straight away (owner-only); without it the callout shows no button. */
	onSwitchBudget?: (m: FastModelResult) => void;
}) {
	const q = useQuery(fastBenchmarksQuery);
	const currency = useCurrency();
	if (q.isPending) return null;
	if (q.isError) return <ErrorState error={q.error} title={t.picker.error} onRetry={() => void q.refetch()} />;
	const rows = q.data.benchmarks?.models ?? [];
	if (!q.data.available || rows.length === 0) return null;
	const offer = budgetOffer(q.data.benchmarks, { model, hosts });
	return (
		<section aria-label={t.picker.title} className="grid gap-2 rounded-md border border-border bg-muted p-3">
			<div className="flex flex-wrap items-baseline justify-between gap-2">
				<h3 className="m-0 text-[13px] font-semibold">{t.picker.title}</h3>
				<Link to="/benchmarks" className="text-[12.5px] text-primary hover:underline">
					{t.picker.more}
				</Link>
			</div>
			{offer ? <BudgetCallout offer={offer} onSwitch={onSwitchBudget} /> : null}
			<p className="m-0 text-[12.5px] text-muted-foreground">{t.picker.lead}</p>
			<ul aria-label={t.picker.listLabel} className="m-0 grid list-none gap-2 p-0">
				{rows.map((m) => {
					const selected = m.model === model && sameHosts(m.provider_order, hosts);
					return (
						<li
							key={m.key}
							data-selected={selected || undefined}
							className="grid gap-1.5 rounded-md border border-border bg-card p-2.5"
						>
							<div className="flex flex-wrap items-center gap-1.5">
								<code className="font-mono text-[12.5px] font-semibold break-all">{m.model}</code>
								{m.provider_order.length > 0 ? (
									<small className="font-mono text-muted-foreground">
										{fill(t.picker.host, { hosts: m.provider_order.join(", ") })}
									</small>
								) : null}
								<TagBadges tags={fastTags(q.data.benchmarks, m)} />
								{selected ? <Badge variant="outline">{t.picker.selected}</Badge> : null}
								<span className="ml-auto" />
								<Button
									type="button"
									size="sm"
									disabled={readOnly || selected}
									aria-label={fill(t.picker.useLabel, { model: m.key })}
									onClick={() => onPick(m)}
								>
									{t.picker.use}
								</Button>
							</div>
							<div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[12.5px] text-foreground-2 tabular-nums">
								<span>{fill(t.picker.accuracy, { v: pct(m.overall) })}</span>
								<span>{fill(t.picker.p90, { v: seconds(m.latency_s.p90) })}</span>
								<span>{fill(t.picker.cost, { v: perThousand(m.usd_per_1000_labels, currency) })}</span>
								<ThinkingNote model={m} />
							</div>
							<BelowBar model={m} />
						</li>
					);
				})}
			</ul>
		</section>
	);
}
