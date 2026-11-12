import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Panel } from "@/features/kbs/ui";
import { DayBars, type DayPoint } from "@/features/overview/QuestionsChart";
import { formatDate } from "@/lib/format/date";
import { type DayRange, dayMs } from "@/lib/format/range";
import { fill } from "@/lib/strings/en";
import { usageStrings as t } from "@/lib/strings/overview";

/** Questions per day for the filtered range (always from `group_by=day`, whatever the table groups by). */
export function UsageChart({
	range,
	points,
	pending,
	error,
	onRetry,
}: {
	range: DayRange;
	points: DayPoint[] | undefined;
	pending: boolean;
	error: unknown;
	onRetry: () => void;
}) {
	const period = fill(t.chart.sub, { from: formatDate(dayMs(range.start)), to: formatDate(dayMs(range.end)) });
	return (
		<Panel title={t.chart.title} sub={period}>
			<div className="p-4 pb-2">
				{error ? (
					<ErrorState error={error} title={t.error} onRetry={onRetry} />
				) : pending || !points ? (
					<CardSkeleton lines={5} />
				) : (
					<DayBars points={points} period={period} />
				)}
			</div>
		</Panel>
	);
}
