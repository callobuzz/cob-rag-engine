/**
 * Date ranges for Overview and Usage (D2). The usage table is kept per UTC day (`usage_daily.day`),
 * so `GET /usage` takes UTC dates (YYYY-MM-DD); `/analytics/queries/*` takes instants.
 *
 * A range picked in local time ("today", "last 7 days") becomes the UTC days that cover it: from
 * the UTC date of the first local midnight to the UTC date of now. In India (UTC+5:30) "today"
 * after 5:30 am therefore spans two UTC days; the usage table has no finer grain. The previous
 * range is the same number of UTC days immediately before, so the two never overlap.
 */

export type OverviewRange = "today" | "7d" | "30d";
export type UsagePreset = "7d" | "30d" | "90d" | "month" | "lastMonth" | "custom";

export const OVERVIEW_RANGES: OverviewRange[] = ["today", "7d", "30d"];
export const USAGE_PRESETS: UsagePreset[] = ["7d", "30d", "90d", "month", "lastMonth", "custom"];

/** The engine refuses more (`GET /usage`: `(end - start).days > 366` → 422). */
export const MAX_RANGE_DAYS = 366;

const DAY = 86_400_000;

export interface DayRange {
	/** First UTC day, YYYY-MM-DD. */
	start: string;
	/** Last UTC day, YYYY-MM-DD. */
	end: string;
}

export interface InstantRange {
	/** ISO instants for `/analytics/queries/*`. */
	start: string;
	end: string;
}

/** YYYY-MM-DD of an instant in UTC. */
export function utcDate(at: number | Date): string {
	return new Date(typeof at === "number" ? at : at.getTime()).toISOString().slice(0, 10);
}

/** Epoch ms of a YYYY-MM-DD at 00:00 UTC. */
export function dayMs(day: string): number {
	return Date.parse(`${day}T00:00:00Z`);
}

export function addDays(day: string, n: number): string {
	return utcDate(dayMs(day) + n * DAY);
}

/** Inclusive number of days from start to end. */
export function spanDays(r: DayRange): number {
	return Math.round((dayMs(r.end) - dayMs(r.start)) / DAY) + 1;
}

/** Every UTC day in the range, oldest first (for zero-filling a per-day chart). */
export function daysIn(r: DayRange): string[] {
	const out: string[] = [];
	for (let d = dayMs(r.start), last = dayMs(r.end); d <= last; d += DAY) out.push(utcDate(d));
	return out;
}

/** Local midnight `daysAgo` local days before `now`. */
function localMidnight(now: number, daysAgo: number): number {
	const d = new Date(now);
	d.setHours(0, 0, 0, 0);
	d.setDate(d.getDate() - daysAgo);
	return d.getTime();
}

const LENGTH: Record<"today" | "7d" | "30d" | "90d", number> = { today: 1, "7d": 7, "30d": 30, "90d": 90 };

/** "Today" / "last N days" in local time as the UTC days that cover it, and as instants. */
export function lastDays(
	preset: "today" | "7d" | "30d" | "90d",
	now: number = Date.now(),
): { days: DayRange; instants: InstantRange } {
	const from = localMidnight(now, LENGTH[preset] - 1);
	return {
		days: { start: utcDate(from), end: utcDate(now) },
		instants: { start: new Date(from).toISOString(), end: new Date(now).toISOString() },
	};
}

/** The same number of UTC days immediately before `r` (for "vs previous" deltas). */
export function previousDays(r: DayRange): DayRange {
	const end = addDays(r.start, -1);
	return { start: addDays(end, -(spanDays(r) - 1)), end };
}

/** The same duration immediately before `r`. */
export function previousInstants(r: InstantRange): InstantRange {
	const start = Date.parse(r.start);
	const length = Date.parse(r.end) - start;
	return { start: new Date(start - length).toISOString(), end: new Date(start).toISOString() };
}

/** First day of the current UTC month (the plan's question count is per UTC month, A6). */
export function monthStartUtc(now: number = Date.now()): string {
	return `${utcDate(now).slice(0, 7)}-01`;
}

/**
 * A Usage preset → UTC days. "This month" and "last month" are calendar months of the UTC date
 * (the same months the plan counts). `custom` uses `from`/`to` as given.
 */
export function usageRange(
	preset: UsagePreset,
	custom: { from?: string; to?: string } = {},
	now: number = Date.now(),
): DayRange {
	const today = utcDate(now);
	switch (preset) {
		case "month":
			return { start: monthStartUtc(now), end: today };
		case "lastMonth": {
			const end = addDays(monthStartUtc(now), -1);
			return { start: `${end.slice(0, 7)}-01`, end };
		}
		case "custom":
			return { start: custom.from ?? addDays(today, -6), end: custom.to ?? today };
		default:
			return lastDays(preset, now).days;
	}
}

export type RangeProblem = "invalid" | "order" | "tooLong" | null;

/** Checks a custom range before it is sent (the engine answers 422 for the same cases). */
export function checkRange(r: DayRange): RangeProblem {
	const re = /^\d{4}-\d{2}-\d{2}$/;
	if (!re.test(r.start) || !re.test(r.end) || Number.isNaN(dayMs(r.start)) || Number.isNaN(dayMs(r.end))) {
		return "invalid";
	}
	if (dayMs(r.start) > dayMs(r.end)) return "order";
	if (spanDays(r) - 1 > MAX_RANGE_DAYS) return "tooLong";
	return null;
}
