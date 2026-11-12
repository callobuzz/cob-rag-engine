import type { Granularity, TrendPoint } from "./api";

/** en-IN label for a `date_trunc` bucket: "29 Sep, 6 pm" · "29 Sep" · "w/c 28 Sep" · "Sep 2026". */
export function bucketLabel(bucket: string, g: Granularity): string {
	const d = new Date(bucket);
	if (Number.isNaN(d.getTime())) return bucket;
	switch (g) {
		case "hour":
			return d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric" });
		case "day":
			return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
		case "week":
			return `w/c ${d.toLocaleDateString("en-IN", { day: "numeric", month: "short" })}`;
		case "month":
			return d.toLocaleDateString("en-IN", { month: "short", year: "numeric" });
	}
}

/** Average of per-bucket averages weighted by each bucket's question count (null when none). */
export function weightedAverage(
	points: TrendPoint[],
	pick: (p: TrendPoint) => number | null | undefined,
): number | null {
	let sum = 0;
	let n = 0;
	for (const p of points) {
		const v = pick(p);
		if (v == null) continue;
		sum += v * p.count;
		n += p.count;
	}
	return n ? sum / n : null;
}

/** The bucket with the most questions (the latest on ties), or null. */
export function busiest(points: TrendPoint[]): TrendPoint | null {
	let best: TrendPoint | null = null;
	for (const p of points) if (p.count > 0 && (!best || p.count >= best.count)) best = p;
	return best;
}

/** Suggested grouping for a period, so a chart has a readable number of columns. */
export function defaultGranularity(range: "7d" | "30d" | "90d" | "all"): Granularity {
	return range === "7d" || range === "30d" ? "day" : range === "90d" ? "week" : "month";
}
