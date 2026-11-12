import { averageConfidence } from "@/features/kbs/detail/OverviewTab";
import { formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";

export { averageConfidence };

/**
 * KPI maths for the Overview (D2). A delta is `null` whenever it can't be computed honestly
 * (no previous value), so the card says "—"/"no previous" instead of "NaN %" or "∞ %".
 */
export function relativeDelta(now: number, prev: number): number | null {
	if (!Number.isFinite(now) || !Number.isFinite(prev) || prev <= 0) return null;
	return (now - prev) / prev;
}

/** Confidence change in percentage points (0.91 vs 0.88 → 3). */
export function pointsDelta(now: number | null, prev: number | null): number | null {
	if (now == null || prev == null) return null;
	return Math.round((now - prev) * 100);
}

export type Direction = "up" | "down" | "same";

export interface DeltaText {
	dir: Direction;
	/** "▲ 12%" — the arrow is decorative, `sr` carries the words. */
	short: string;
	/** "up 12%" for screen readers (and never colour alone). */
	sr: string;
}

export function describeDelta(delta: number | null, unit: "pct" | "pts"): DeltaText | null {
	if (delta == null || Number.isNaN(delta)) return null;
	const k = overviewStrings.kpi;
	const size = unit === "pct" ? formatPercent(Math.abs(delta)) : fill(k.pts, { n: Math.abs(delta) });
	const rounded = unit === "pct" ? Math.round(delta * 100) : delta;
	if (rounded === 0) return { dir: "same", short: `● ${k.same}`, sr: k.same };
	const dir: Direction = delta > 0 ? "up" : "down";
	return { dir, short: `${dir === "up" ? "▲" : "▼"} ${size}`, sr: fill(dir === "up" ? k.up : k.down, { v: size }) };
}

export function totalTokens(u: { prompt_tokens: number; completion_tokens: number } | undefined): number | null {
	return u ? u.prompt_tokens + u.completion_tokens : null;
}

export function sumDocuments(items: { document_count?: number }[]): number {
	return items.reduce((n, c) => n + (c.document_count ?? 0), 0);
}

/** "Good morning/afternoon/evening" by the local hour. */
export function greetingKey(hour: number): "morning" | "afternoon" | "evening" {
	if (hour >= 5 && hour < 12) return "morning";
	if (hour >= 12 && hour < 17) return "afternoon";
	return "evening";
}

/** "1 knowledge base" / "3 knowledge bases" (strings with `one` / `many`). */
export function plural(forms: { one: string; many: string }, n: number, formatted: string = String(n)): string {
	return n === 1 ? forms.one : fill(forms.many, { n: formatted });
}
