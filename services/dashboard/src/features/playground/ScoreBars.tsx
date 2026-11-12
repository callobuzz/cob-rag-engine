import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { cn } from "@/lib/utils";
import type { ScoreBreakdown } from "./api";

/**
 * Mockup `.bars`: meaning (`dense_score`), keyword (`sparse_score`), blended (`fused_score`) and
 * re-rank (`rerank_score`, when present). Each signal is on its own scale (BM25 is unbounded, RRF
 * is tiny), so a bar is the value over that signal's largest value among the shown passages; the
 * number next to it is the engine's raw score. The bar is decorative: the text carries the value.
 */
const COLS: Record<number, string> = {
	1: "grid-cols-1",
	3: "min-[521px]:grid-cols-3",
	4: "min-[521px]:grid-cols-4",
};

export type Signal = "dense" | "sparse" | "fused" | "rerank";

export const SIGNALS: { key: Signal; label: string; color: string }[] = [
	{ key: "dense", label: t.sources.meaning, color: "var(--chart-1)" },
	{ key: "sparse", label: t.sources.keyword, color: "var(--chart-2)" },
	{ key: "fused", label: t.sources.blended, color: "var(--success)" },
	{ key: "rerank", label: t.sources.rerank, color: "var(--violet)" },
];

export function signalValue(b: ScoreBreakdown, key: Signal): number | null {
	const v =
		key === "dense"
			? b.dense_score
			: key === "sparse"
				? b.sparse_score
				: key === "fused"
					? b.fused_score
					: b.rerank_score;
	return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export type SignalMax = Record<Signal, number>;

export function maxima(breakdowns: (ScoreBreakdown | null | undefined)[]): SignalMax {
	const m: SignalMax = { dense: 0, sparse: 0, fused: 0, rerank: 0 };
	for (const b of breakdowns) {
		if (!b) continue;
		for (const s of SIGNALS) {
			const v = signalValue(b, s.key);
			if (v !== null && Math.abs(v) > m[s.key]) m[s.key] = Math.abs(v);
		}
	}
	return m;
}

export function formatScore(v: number): string {
	return Math.abs(v) < 0.1 && v !== 0 ? v.toFixed(3) : v.toFixed(2);
}

export function ScoreBars({ breakdown, max }: { breakdown: ScoreBreakdown; max: SignalMax }) {
	const shown = SIGNALS.filter((s) => signalValue(breakdown, s.key) !== null);
	return (
		<ul className={cn("m-0 grid list-none grid-cols-2 gap-2.5 p-0", COLS[shown.length])}>
			{shown.map((s) => {
				const v = signalValue(breakdown, s.key) ?? 0;
				const width = max[s.key] > 0 ? Math.max(0, Math.min(1, Math.abs(v) / max[s.key])) : 0;
				return (
					<li key={s.key} className="grid min-w-0 gap-[3px] text-[11.5px] text-muted-foreground">
						<span className="truncate">
							<span className="sr-only">{fill(t.sources.scoreLabel, { label: s.label, v: formatScore(v) })}</span>
							<span aria-hidden>
								{s.label} <b className="font-mono font-medium text-foreground-2">{formatScore(v)}</b>
							</span>
						</span>
						<span aria-hidden className="block h-[5px] overflow-hidden rounded-[3px] bg-secondary">
							<i className="block h-full rounded-[3px]" style={{ width: `${width * 100}%`, background: s.color }} />
						</span>
					</li>
				);
			})}
		</ul>
	);
}
