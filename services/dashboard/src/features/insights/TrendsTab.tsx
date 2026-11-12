import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Segmented } from "@/components/patterns/Segmented";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Kpi, Panel } from "@/features/kbs/ui";
import { formatNumber, formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import { countQuery, type Granularity, RANGES, type Range, rangeStart, trendsQuery } from "./api";
import { ChartOrTable, Columns, type Point, RatioLine, SimpleTable } from "./charts";
import type { ScopeProps } from "./InsightsPage";
import { formatLatency } from "./QuestionsTab";
import { bucketLabel, busiest, defaultGranularity, weightedAverage } from "./trends";
import { FilterRow } from "./ui";

const t = insightsStrings.trends;
const GRANULARITIES: Granularity[] = ["hour", "day", "week", "month"];
const UNIT: Record<Granularity, string> = { hour: "hour", day: "day", week: "week", month: "month" };

/**
 * Trends tab: `GET /analytics/queries/trends` (granularity + period) as two charts — questions
 * per bucket and average confidence per bucket (never one chart with two y-axes) — with a table
 * toggle; the total from `GET /analytics/queries/count`.
 */
export function TrendsTab({ corpusId }: ScopeProps) {
	const [range, setRange] = useState<Range>("30d");
	const [granularity, setGranularity] = useState<Granularity>("day");
	const start = rangeStart(range);
	const q = useQuery(trendsQuery({ corpusId, start, granularity }));
	const count = useQuery(countQuery({ corpusId, start }));
	const period = insightsStrings.ranges[range];

	const data = q.data?.data ?? [];
	const countPoints: Point[] = data.map((p) => ({
		key: p.bucket,
		label: bucketLabel(p.bucket, granularity),
		value: p.count,
	}));
	const confPoints: Point[] = data.map((p) => ({
		key: p.bucket,
		label: bucketLabel(p.bucket, granularity),
		value: p.avg_confidence ?? null,
	}));
	const top = busiest(data);
	const countTitle = fill(t.countChart, { unit: UNIT[granularity] });
	const confTitle = fill(t.confidenceChart, { unit: UNIT[granularity] });
	const scored = confPoints.filter((p) => p.value != null).map((p) => p.value as number);
	const countSummary = top
		? fill(t.summaryCount, {
				title: countTitle,
				period,
				total: formatNumber(data.reduce((a, p) => a + p.count, 0)),
				max: formatNumber(top.count),
				at: bucketLabel(top.bucket, granularity),
			})
		: fill(t.summaryCountEmpty, { title: countTitle, period });
	const confSummary = scored.length
		? fill(t.summaryConfidence, {
				title: confTitle,
				period,
				min: formatPercent(Math.min(...scored)),
				max: formatPercent(Math.max(...scored)),
			})
		: fill(t.summaryConfidenceEmpty, { title: confTitle, period });

	const table = (
		<SimpleTable
			caption={t.tableLabel}
			headers={[t.colBucket, t.colCount, t.colConfidence, t.colLatency]}
			rows={data.map((p) => ({
				key: p.bucket,
				cells: [
					bucketLabel(p.bucket, granularity),
					formatNumber(p.count),
					formatPercent(p.avg_confidence ?? null),
					formatLatency(p.avg_latency ?? null),
				],
			}))}
		/>
	);

	return (
		<>
			<FilterRow className="px-0 py-0">
				<Segmented
					label={insightsStrings.rangeLabel}
					value={range}
					options={RANGES.map((r) => ({ value: r, label: insightsStrings.ranges[r] }))}
					onChange={(r) => {
						setRange(r);
						setGranularity(defaultGranularity(r));
					}}
				/>
				<Segmented
					label={t.granularity}
					value={granularity}
					options={GRANULARITIES.map((g) => ({ value: g, label: t.granularities[g] }))}
					onChange={setGranularity}
				/>
			</FilterRow>
			<div className="grid grid-cols-1 gap-4 min-[600px]:grid-cols-3" data-testid="trend-kpis">
				<Kpi label={t.total} value={count.data ? formatNumber(count.data.count) : "—"} delta={period} />
				<Kpi label={t.avgConfidence} value={formatPercent(weightedAverage(data, (p) => p.avg_confidence))} />
				<Kpi label={t.avgLatency} value={formatLatency(weightedAverage(data, (p) => p.avg_latency))} />
			</div>
			{q.isError ? (
				<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
			) : q.isPending ? (
				<CardSkeleton lines={6} />
			) : data.length === 0 ? (
				<Panel title={t.title}>
					<p className="m-0 p-4 text-[13px] text-muted-foreground">{t.empty}</p>
				</Panel>
			) : (
				<ChartOrTable
					tableLabel={t.tableLabel}
					table={<Panel title={t.tableLabel}>{table}</Panel>}
					chart={
						<div className="grid gap-4 min-[1101px]:grid-cols-2">
							<Panel title={countTitle} sub={period}>
								<div className="p-4 pb-2">
									<Columns
										points={countPoints}
										summary={countSummary}
										format={(v) => formatNumber(v)}
										testId="trend-count-chart"
									/>
								</div>
							</Panel>
							<Panel title={confTitle} sub={t.confidenceNote}>
								<div className="p-4 pb-2">
									<RatioLine
										points={confPoints}
										summary={confSummary}
										format={(v) => formatPercent(v)}
										testId="trend-confidence-chart"
									/>
								</div>
							</Panel>
						</div>
					}
				/>
			)}
		</>
	);
}
