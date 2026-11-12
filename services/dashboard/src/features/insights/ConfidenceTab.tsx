import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Segmented } from "@/components/patterns/Segmented";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Panel } from "@/features/kbs/ui";
import { formatNumber, formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import { type ConfidenceBucket, confidenceQuery, RANGES, type Range, rangeStart } from "./api";
import { ChartOrTable, Columns, SimpleTable } from "./charts";
import type { ScopeProps } from "./InsightsPage";
import { FilterRow } from "./ui";

const t = insightsStrings.confidence;

/** "0.0-0.2" → "0.0–0.2" (en dash for display). */
export const rangeLabel = (r: string) => r.replace("-", "–");

export function confidenceSummary(buckets: ConfidenceBucket[], period: string): string {
	const scored = buckets.reduce((a, b) => a + b.count, 0);
	if (!scored) return fill(t.summaryEmpty, { period });
	const good = buckets.find((b) => b.range.startsWith("0.8"))?.count ?? 0;
	return fill(t.summary, { period, scored: formatNumber(scored), good: formatNumber(good) });
}

/** Confidence tab: `GET /analytics/queries/confidence` → columns of the five buckets + a table. */
export function ConfidenceTab({ corpusId }: ScopeProps) {
	const [range, setRange] = useState<Range>("30d");
	const q = useQuery(confidenceQuery({ corpusId, start: rangeStart(range) }));
	const period = insightsStrings.ranges[range];
	const buckets = q.data?.buckets ?? [];
	const total = buckets.reduce((a, b) => a + b.count, 0);
	const summary = confidenceSummary(buckets, period);

	return (
		<>
			<FilterRow className="px-0 py-0">
				<Segmented
					label={insightsStrings.rangeLabel}
					value={range}
					options={RANGES.map((r) => ({ value: r, label: insightsStrings.ranges[r] }))}
					onChange={setRange}
				/>
			</FilterRow>
			<Panel title={t.title} sub={t.sub}>
				<div className="grid gap-3 p-4 pb-2">
					{q.isError ? (
						<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
					) : q.isPending ? (
						<CardSkeleton lines={5} />
					) : total === 0 ? (
						<p className="m-0 text-[13px] text-muted-foreground">{t.empty}</p>
					) : (
						<>
							<p className="m-0 text-[13px] text-foreground-2" data-testid="confidence-summary">
								{summary}
							</p>
							<ChartOrTable
								tableLabel={t.title}
								chart={
									<Columns
										points={buckets.map((b) => ({ key: b.range, label: rangeLabel(b.range), value: b.count }))}
										summary={summary}
										format={(v) => formatNumber(v)}
										testId="confidence-chart"
									/>
								}
								table={
									<SimpleTable
										caption={summary}
										headers={[t.colRange, t.colCount, t.colShare]}
										rows={buckets.map((b) => ({
											key: b.range,
											cells: [rangeLabel(b.range), formatNumber(b.count), formatPercent(b.count / total)],
										}))}
									/>
								}
							/>
						</>
					)}
					<p className="m-0 text-[12.5px] text-muted-foreground">{t.explain}</p>
				</div>
			</Panel>
		</>
	);
}
