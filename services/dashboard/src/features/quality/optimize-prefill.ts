/**
 * "Copy to Retrieval tab" (D9, G-15: there is no apply endpoint). The optimiser's search space
 * (analytics/optimizer_types.py SEARCH_SPACE) only partly matches a knowledge base's
 * `RetrievalConfigInput`: four parameters map onto per-KB fields; the rest are engine-wide
 * settings and are listed, never sent.
 */
import type { components } from "@/lib/api/schema";

export type RetrievalPrefill = components["schemas"]["RetrievalConfigInput"];

const clampInt = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.round(v)));

const MAP: Record<string, (v: number) => Partial<RetrievalPrefill>> = {
	/** Candidates fetched before fusion. */
	retrieval_top_k_dense: (v) => ({ top_k: clampInt(v, 1, 100) }),
	/** Passages that reach the answer. */
	rerank_top_k_output: (v) => ({ final_k: clampInt(v, 1, 100) }),
	rrf_k: (v) => ({ rrf_k: clampInt(v, 1, 1000) }),
	mmr_lambda: (v) => ({ mmr_lambda: Math.min(1, Math.max(0, Math.round(v * 100) / 100)) }),
};

export function prefillFromParams(params: Record<string, unknown>): {
	prefill: RetrievalPrefill;
	engineWide: string[];
} {
	const prefill: RetrievalPrefill = {};
	const engineWide: string[] = [];
	for (const [name, value] of Object.entries(params)) {
		const map = MAP[name];
		if (map && typeof value === "number" && Number.isFinite(value)) Object.assign(prefill, map(value));
		else engineWide.push(name);
	}
	return { prefill, engineWide };
}

/** `{params, metrics, trial_number}` entries of `pareto_configs` / `best_config` (ParetoConfig.to_dict). */
export interface ParetoEntry {
	params: Record<string, unknown>;
	metrics: Record<string, unknown>;
	trial: number | null;
}

const obj = (v: unknown) => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

export function asParetoEntry(x: unknown): ParetoEntry {
	const o = obj(x);
	return {
		params: obj(o.params),
		metrics: obj(o.metrics),
		trial: typeof o.trial_number === "number" ? o.trial_number : null,
	};
}
