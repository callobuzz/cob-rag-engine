import { fill } from "@/lib/strings/en";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import type { RunDetail } from "../evals-api";
import { metricLabel, pct } from "./format";

/** One card per score: the mean, its 95 % interval, how many questions, the pass mark. */
export function MetricCards({ run }: { run: RunDetail }) {
	if (run.metrics.length === 0) return <p className="m-0 text-foreground-2">{t.detail.noScores}</p>;
	return (
		<ul aria-label={t.detail.scores} className="m-0 grid list-none gap-3 p-0 min-[861px]:grid-cols-3">
			{run.metrics.map((m) => (
				<li key={m.metric} className="rounded-lg border border-border bg-card p-3">
					<p className="m-0 text-[13px] text-foreground-2">{metricLabel(m.metric)}</p>
					<p className="m-0 font-display text-2xl font-bold tabular-nums">{pct(m.mean)}</p>
					<p className="m-0 text-[13px]">
						{fill(t.metricsCards.ci, { low: pct(m.ci_low).replace("%", ""), high: pct(m.ci_high) })}
					</p>
					<p className="m-0 text-[12.5px] text-muted-foreground">
						{fill(t.metricsCards.n, { n: m.n })}
						{m.threshold != null ? ` · ${fill(t.metricsCards.passAt, { t: pct(m.threshold) })}` : ""}
						{m.pass_rate != null ? ` · ${fill(t.metricsCards.passed, { p: pct(m.pass_rate) })}` : ""}
						{m.small_sample ? (
							<span className="ml-2 rounded bg-warning-soft px-1.5 text-warning">{t.metricsCards.smallSample}</span>
						) : null}
					</p>
				</li>
			))}
		</ul>
	);
}
