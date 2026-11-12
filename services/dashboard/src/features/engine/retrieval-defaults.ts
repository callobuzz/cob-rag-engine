import type { RetrievalDefaults, RetrievalDefaultsUpdate } from "./api";

/** Bounds from `UpdateRetrievalConfigRequest` (api/schemas/admin.py). */
export const DEFAULT_FIELDS = [
	{ key: "top_k_dense", min: 1, max: 200, integer: true, step: 1 },
	{ key: "top_k_sparse", min: 1, max: 200, integer: true, step: 1 },
	{ key: "rrf_k", min: 1, max: 1000, integer: true, step: 1 },
	{ key: "mmr_lambda", min: 0, max: 1, integer: false, step: 0.05 },
	{ key: "temporal_decay_half_life_days", min: 0, max: null, integer: false, step: 1, exclusiveMin: true },
	{ key: "rerank_top_n_input", min: 1, max: 500, integer: true, step: 1 },
	{ key: "rerank_top_k_output", min: 1, max: 200, integer: true, step: 1 },
] as const satisfies readonly {
	key: keyof RetrievalDefaultsUpdate;
	min: number;
	max: number | null;
	integer: boolean;
	step: number;
	exclusiveMin?: boolean;
}[];

export type DefaultKey = (typeof DEFAULT_FIELDS)[number]["key"];
export type DefaultValues = Record<DefaultKey, string>;

// Compile-time check: every field of the request is in DEFAULT_FIELDS and nothing else.
type Missing = Exclude<keyof RetrievalDefaultsUpdate, DefaultKey>;
const _exhaustive: Missing extends never ? true : never = true;
void _exhaustive;

export function defaultValues(d: RetrievalDefaults): DefaultValues {
	return Object.fromEntries(DEFAULT_FIELDS.map((f) => [f.key, String(d[f.key])])) as DefaultValues;
}

export type FieldProblem =
	| { kind: "between"; min: number; max: number }
	| { kind: "above"; min: number }
	| { kind: "whole" };

/** A field's text → a problem, or null when valid. */
export function checkField(key: DefaultKey, text: string): FieldProblem | null {
	const f = DEFAULT_FIELDS.find((x) => x.key === key);
	if (!f) return null;
	const n = Number(text.trim());
	const range = (): FieldProblem =>
		f.max == null ? { kind: "above", min: f.min } : { kind: "between", min: f.min, max: f.max };
	if (text.trim() === "" || !Number.isFinite(n)) return range();
	if (f.integer && !Number.isInteger(n)) return { kind: "whole" };
	if ("exclusiveMin" in f && f.exclusiveMin ? n <= f.min : n < f.min) return range();
	if (f.max != null && n > f.max) return range();
	return null;
}

/** Changed fields only, as numbers (never null). */
export function defaultsPatch(initial: DefaultValues, v: DefaultValues): RetrievalDefaultsUpdate {
	const out: RetrievalDefaultsUpdate = {};
	for (const f of DEFAULT_FIELDS) {
		if (Number(v[f.key]) !== Number(initial[f.key])) out[f.key] = Number(v[f.key]);
	}
	return out;
}
