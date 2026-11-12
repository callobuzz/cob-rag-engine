import { qualityStrings } from "@/lib/strings/insights";

/**
 * Evaluation `metrics` (evaluation/runner.py): `{ <name>: { name, score, threshold, passed } }`
 * with RAGAS names; unknown names are shown as they come, without an explanation.
 */
export interface MetricRow {
	key: string;
	label: string;
	meaning: string | null;
	score: number | null;
	threshold: number | null;
	passed: boolean | null;
}

const KNOWN: Record<string, { label: string; meaning: string }> = qualityStrings.metrics;
const ORDER = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"];

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function metricRows(metrics: Record<string, unknown>): MetricRow[] {
	const rows = Object.entries(metrics).map(([key, raw]): MetricRow => {
		const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : { score: raw };
		const score = num(o.score);
		const threshold = num(o.threshold);
		const passed =
			typeof o.passed === "boolean" ? o.passed : score != null && threshold != null ? score >= threshold : null;
		const known = KNOWN[key];
		return { key, label: known?.label ?? key, meaning: known?.meaning ?? null, score, threshold, passed };
	});
	const rank = (k: string) => (ORDER.includes(k) ? ORDER.indexOf(k) : ORDER.length);
	return rows.sort((a, b) => rank(a.key) - rank(b.key) || a.key.localeCompare(b.key));
}
