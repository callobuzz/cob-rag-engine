import { type ReactNode, useId, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Button } from "@/components/ui/button";
import { formatCompact } from "@/lib/format/number";
import { insightsStrings } from "@/lib/strings/insights";

/**
 * D9 charts (dataviz skill). One series per chart, so no legend box (the title names it); one
 * y-axis per chart — count and average confidence are two charts, never a dual axis. Marks in
 * `--chart-1` (validated in light and dark), columns ≤ 28 px with 4 px rounded tops on the
 * baseline, a 2 px line with 8 px hover markers, hairline horizontal grid, muted axes, a tooltip
 * per mark (crosshair on the line), and a table view with the same numbers. Each plot is
 * `role="img"` with a text summary.
 */

const tick = { fill: "var(--muted-foreground)", fontSize: 11 };

function reducedMotion(): boolean {
	return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

export interface Point {
	key: string;
	label: string;
	value: number | null;
}

function PointTooltip({
	active,
	payload,
	format,
}: {
	active?: boolean;
	payload?: readonly { payload?: Point }[];
	format: (v: number | null) => string;
}) {
	const p = active ? payload?.[0]?.payload : undefined;
	if (!p) return null;
	return (
		<div className="grid gap-0.5 rounded-md border border-border bg-popover px-3 py-2 text-[12.5px] shadow-md">
			<b className="font-display text-[15px] text-foreground tabular-nums">{format(p.value)}</b>
			<span className="text-muted-foreground">{p.label}</span>
		</div>
	);
}

/** Columns over categories or time buckets. */
export function Columns({
	points,
	summary,
	format,
	height = 220,
	testId,
}: {
	points: Point[];
	summary: string;
	format: (v: number | null) => string;
	height?: number;
	testId?: string;
}) {
	return (
		<div role="img" aria-label={summary} data-testid={testId} style={{ height }}>
			<ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height }}>
				<BarChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} accessibilityLayer={false}>
					<CartesianGrid vertical={false} stroke="var(--border)" strokeWidth={1} />
					<XAxis
						dataKey="label"
						tickLine={false}
						axisLine={{ stroke: "var(--border)" }}
						tick={tick}
						minTickGap={14}
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
						content={(props) => <PointTooltip active={props.active} payload={props.payload} format={format} />}
						isAnimationActive={false}
					/>
					<Bar
						dataKey="value"
						fill="var(--chart-1)"
						radius={[4, 4, 0, 0]}
						maxBarSize={28}
						isAnimationActive={!reducedMotion()}
						activeBar={{ fill: "var(--chart-1)", fillOpacity: 0.8 }}
					/>
				</BarChart>
			</ResponsiveContainer>
		</div>
	);
}

/** A 0–1 measure over time (average confidence), shown as a percentage; gaps where there is no value. */
export function RatioLine({
	points,
	summary,
	format,
	height = 200,
	testId,
}: {
	points: Point[];
	summary: string;
	format: (v: number | null) => string;
	height?: number;
	testId?: string;
}) {
	return (
		<div role="img" aria-label={summary} data-testid={testId} style={{ height }}>
			<ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height }}>
				<LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }} accessibilityLayer={false}>
					<CartesianGrid vertical={false} stroke="var(--border)" strokeWidth={1} />
					<XAxis
						dataKey="label"
						padding={{ left: 16, right: 16 }}
						tickLine={false}
						axisLine={{ stroke: "var(--border)" }}
						tick={tick}
						minTickGap={14}
						interval="preserveStartEnd"
					/>
					<YAxis
						domain={[0, 1]}
						ticks={[0, 0.25, 0.5, 0.75, 1]}
						tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
						tickLine={false}
						axisLine={false}
						tick={tick}
						width={44}
					/>
					<Tooltip
						cursor={{ stroke: "var(--input)", strokeWidth: 1 }}
						content={(props) => <PointTooltip active={props.active} payload={props.payload} format={format} />}
						isAnimationActive={false}
					/>
					<Line
						type="linear"
						dataKey="value"
						stroke="var(--chart-1)"
						strokeWidth={2}
						dot={points.length <= 1 ? { r: 4, fill: "var(--chart-1)", stroke: "var(--card)", strokeWidth: 2 } : false}
						activeDot={{ r: 4, fill: "var(--chart-1)", stroke: "var(--card)", strokeWidth: 2 }}
						connectNulls={false}
						isAnimationActive={!reducedMotion()}
					/>
				</LineChart>
			</ResponsiveContainer>
		</div>
	);
}

/** "Show as table" / "Show as chart" around a chart; the table carries the same numbers. */
export function ChartOrTable({ chart, table, tableLabel }: { chart: ReactNode; table: ReactNode; tableLabel: string }) {
	const [asTable, setAsTable] = useState(false);
	const id = useId();
	const t = insightsStrings.trends;
	return (
		<div className="grid gap-2">
			{asTable ? (
				<section
					id={id}
					aria-label={tableLabel}
					// biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable table must be reachable by keyboard (axe scrollable-region-focusable)
					tabIndex={0}
					className="max-h-[360px] overflow-auto"
				>
					{table}
				</section>
			) : (
				chart
			)}
			<div className="flex justify-end">
				<Button
					type="button"
					size="sm"
					variant="ghost"
					aria-pressed={asTable}
					aria-controls={asTable ? id : undefined}
					onClick={() => setAsTable((v) => !v)}
				>
					{asTable ? t.showChart : t.showTable}
				</Button>
			</div>
		</div>
	);
}

/** A plain data table for a chart's table view (first column = row headers). */
export function SimpleTable({
	caption,
	headers,
	rows,
}: {
	caption: string;
	headers: string[];
	rows: { key: string; cells: ReactNode[] }[];
}) {
	return (
		<table className="w-full border-collapse text-[13px]">
			<caption className="sr-only">{caption}</caption>
			<thead>
				<tr>
					{headers.map((h, i) => (
						<th
							key={h}
							scope="col"
							className={`density-cell border-b border-border font-semibold ${i === 0 ? "text-left" : "text-right"}`}
						>
							{h}
						</th>
					))}
				</tr>
			</thead>
			<tbody>
				{rows.map((r) => (
					<tr key={r.key} className="border-b border-border last:border-b-0">
						{r.cells.map((c, i) =>
							i === 0 ? (
								// biome-ignore lint/suspicious/noArrayIndexKey: fixed column order
								<th key={i} scope="row" className="density-cell text-left font-normal">
									{c}
								</th>
							) : (
								// biome-ignore lint/suspicious/noArrayIndexKey: fixed column order
								<td key={i} className="density-cell text-right tabular-nums">
									{c}
								</td>
							),
						)}
					</tr>
				))}
			</tbody>
		</table>
	);
}
