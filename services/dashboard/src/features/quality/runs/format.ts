import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";

/** A 0-1 score as a whole percent ("62%"); "—" when there is none. */
export const pct = (v: number | null | undefined): string => (v == null ? "—" : `${Math.round(v * 100)}%`);

/** "Sticks to the documents"; a criterion ("criterion:mentions-home-fee") shows its own name. */
export function metricLabel(metric: string): string {
	if (metric.startsWith("criterion:")) return metric.slice("criterion:".length);
	return t.metrics[metric] ?? metric;
}

/** Totals of a run as the engine stores them: `{questions, passed, ...}`. */
export function runResult(
	totals: Record<string, unknown> | null | undefined,
): { passed: number; total: number } | null {
	if (!totals) return null;
	const total = Number(totals.questions);
	if (!Number.isFinite(total)) return null;
	return { passed: Number(totals.passed ?? 0), total };
}
