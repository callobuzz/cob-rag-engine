import { type ReactNode, useId, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { formatDate, formatShortDate } from "@/lib/format/date";
import { formatCompact, formatNumber } from "@/lib/format/number";
import { dayMs } from "@/lib/format/range";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";

/**
 * Questions per day (dataviz skill): one series, so no legend box (the title names it); columns
 * in `--chart-1` (validated against both card surfaces), ≤ 24 px wide with 4 px rounded tops on
 * the baseline, hairline solid horizontal grid, en-IN axis, a per-bar tooltip, and a "Show as
 * table" toggle with the same numbers. The plot is `role="img"` with a text summary.
 */
export interface DayPoint {
	day: string;
	requests: number;
}

const t = overviewStrings.chart;

/** Busiest day (the latest one on ties), or null when nothing was asked. */
export function busiestDay(points: DayPoint[]): DayPoint | null {
	let best: DayPoint | null = null;
	for (const p of points) if (p.requests > 0 && (!best || p.requests >= best.requests)) best = p;
	return best;
}

/** "Questions per day, last 7 days: highest 482 on 29 Sep." */
export function chartSummary(points: DayPoint[], period: string): string {
	const top = busiestDay(points);
	return top
		? fill(t.summary, { period, max: formatNumber(top.requests), day: formatShortDate(dayMs(top.day)) })
		: fill(t.summaryEmpty, { period });
}

function reducedMotion(): boolean {
	return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

function DayTooltip({ active, payload }: { active?: boolean; payload?: readonly { payload?: DayPoint }[] }) {
	const p = active ? payload?.[0]?.payload : undefined;
	if (!p) return null;
	return (
		<div className="grid gap-0.5 rounded-md border border-border bg-popover px-3 py-2 text-[12.5px] shadow-md">
			<b className="font-display text-[15px] text-foreground tabular-nums">{formatNumber(p.requests)}</b>
			<span className="text-muted-foreground">{formatDate(dayMs(p.day))}</span>
		</div>
	);
}

export function DayBars({ points, period, height = 220 }: { points: DayPoint[]; period: string; height?: number }) {
	const [asTable, setAsTable] = useState(false);
	const tableId = useId();
	const summary = chartSummary(points, period);
	const tick = { fill: "var(--muted-foreground)", fontSize: 11 };
	return (
		<div className="grid gap-2">
			{asTable ? (
				<section
					id={tableId}
					aria-label={t.tableLabel}
					// biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable table must be reachable by keyboard (axe scrollable-region-focusable)
					tabIndex={0}
					className="max-h-[320px] overflow-auto"
				>
					<table className="w-full border-collapse text-[13px]">
						<caption className="sr-only">{summary}</caption>
						<thead>
							<tr>
								<th scope="col" className="density-cell border-b border-border text-left font-semibold">
									{t.colDay}
								</th>
								<th scope="col" className="density-cell border-b border-border text-right font-semibold">
									{t.colQuestions}
								</th>
							</tr>
						</thead>
						<tbody>
							{points.map((p) => (
								<tr key={p.day} className="border-b border-border last:border-b-0">
									<th scope="row" className="density-cell text-left font-normal">
										{formatDate(dayMs(p.day))}
									</th>
									<td className="density-cell text-right tabular-nums">{formatNumber(p.requests)}</td>
								</tr>
							))}
						</tbody>
					</table>
				</section>
			) : (
				<div role="img" aria-label={summary} data-testid="day-bars" style={{ height }}>
					<ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height }}>
						<BarChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} accessibilityLayer={false}>
							<CartesianGrid vertical={false} stroke="var(--border)" strokeWidth={1} />
							<XAxis
								dataKey="day"
								tickFormatter={(d: string) => formatShortDate(dayMs(d))}
								tickLine={false}
								axisLine={{ stroke: "var(--border)" }}
								tick={tick}
								minTickGap={18}
								interval="preserveStartEnd"
							/>
							<YAxis
								allowDecimals={false}
								tickFormatter={(v: number) => formatCompact(v)}
								tickLine={false}
								axisLine={false}
								tick={tick}
								width={44}
							/>
							<Tooltip
								cursor={{ fill: "var(--accent)" }}
								content={(props) => <DayTooltip active={props.active} payload={props.payload} />}
								isAnimationActive={false}
							/>
							<Bar
								dataKey="requests"
								name={t.colQuestions}
								fill="var(--chart-1)"
								radius={[4, 4, 0, 0]}
								maxBarSize={24}
								isAnimationActive={!reducedMotion()}
								activeBar={{ fill: "var(--chart-1)", fillOpacity: 0.8 }}
							/>
						</BarChart>
					</ResponsiveContainer>
				</div>
			)}
			<div className="flex justify-end">
				<Button
					type="button"
					size="sm"
					variant="ghost"
					aria-pressed={asTable}
					aria-controls={asTable ? tableId : undefined}
					onClick={() => setAsTable((v) => !v)}
				>
					{asTable ? t.showChart : t.showTable}
				</Button>
			</div>
		</div>
	);
}

/** Overview card: the chart plus the per-source totals row under it (mockup `.chart-sum`). */
export function QuestionsChart({
	points,
	period,
	pending,
	error,
	onRetry,
	footer,
}: {
	points: DayPoint[] | undefined;
	period: string;
	pending: boolean;
	error: unknown;
	onRetry: () => void;
	footer?: ReactNode;
}) {
	const top = points ? busiestDay(points) : null;
	return (
		<section aria-label={t.title} className="min-w-0 rounded-lg border border-border bg-card">
			<div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3.5">
				<div className="min-w-0">
					<h2 className="text-[15px] font-bold">{t.title}</h2>
					<div className="text-[12.5px] text-muted-foreground">
						{top
							? fill(t.busiest, {
									day: formatShortDate(dayMs(top.day)),
									n: fill(overviewStrings.questionsWord.many, { n: formatNumber(top.requests) }),
								})
							: points
								? t.busiestNone
								: t.sub}
					</div>
				</div>
			</div>
			<div className="p-4 pb-2">
				{error ? (
					<ErrorState error={error} title={t.error} onRetry={onRetry} />
				) : pending || !points ? (
					<CardSkeleton lines={5} />
				) : (
					<DayBars points={points} period={period} />
				)}
			</div>
			{footer}
		</section>
	);
}
