import type { components } from "@/lib/api/schema";

/**
 * Every advanced retrieval field (D3 "Retrieval" table): label, plain help, control and bounds
 * (bounds from docs/openapi.json `RetrievalConfigInput`). `RetrievalField` keys must be exactly
 * the keys of `RetrievalConfigInput` minus `preset` — enforced at compile time below and in
 * retrieval-fields.test.ts.
 */
export type RetrievalConfigInput = components["schemas"]["RetrievalConfigInput"];
export type RetrievalKey = Exclude<keyof RetrievalConfigInput, "preset">;

export type FieldKind =
	| { kind: "switch" }
	| { kind: "number"; min: number; max: number; step: number; integer: boolean }
	| { kind: "select"; options: { value: string; label: string }[] }
	| { kind: "text"; maxLength?: number };

export interface RetrievalFieldDef {
	key: RetrievalKey;
	label: string;
	help: string;
	control: FieldKind;
	/** Only meaningful when another field has this value (shown dimmed otherwise). */
	dependsOn?: { key: RetrievalKey; value: boolean | string };
}

const int = (min: number, max: number): FieldKind => ({ kind: "number", min, max, step: 1, integer: true });

export const RETRIEVAL_FIELDS = [
	{
		key: "hybrid_search_enabled",
		label: "Keyword search alongside meaning search",
		help: 'Finds exact words like "HbA1c" and package codes.',
		control: { kind: "switch" },
	},
	{
		key: "hybrid_dense_weight",
		label: "Weight of meaning search",
		help: "How the two lists are blended; the rest goes to keywords.",
		control: { kind: "number", min: 0, max: 1, step: 0.05, integer: false },
		dependsOn: { key: "fusion_strategy", value: "convex" },
	},
	{
		key: "fusion_strategy",
		label: "Blending method",
		help: "Weighted scores (measured default) or rank fusion (RRF).",
		control: {
			kind: "select",
			options: [
				{ value: "convex", label: "Weighted scores" },
				{ value: "rrf", label: "Rank fusion (RRF)" },
			],
		},
	},
	{
		key: "rrf_k",
		label: "RRF constant",
		help: "Only for rank fusion.",
		control: int(1, 1000),
		dependsOn: { key: "fusion_strategy", value: "rrf" },
	},
	{ key: "top_k", label: "Passages searched", help: "How wide the first search is.", control: int(1, 100) },
	{
		key: "final_k",
		label: "Passages given to the model",
		help: "The best few after all steps.",
		control: int(1, 100),
	},
	{
		key: "multi_query_count",
		label: "Rephrasings of the question",
		help: "Written by the fast model.",
		control: int(1, 5),
	},
	{
		key: "hyde_enabled",
		label: "Hypothetical answer search (HyDE)",
		help: "Searches with a drafted answer.",
		control: { kind: "switch" },
	},
	{
		key: "query_classification_enabled",
		label: "Question type detection",
		help: "Routes by category.",
		control: { kind: "switch" },
	},
	{
		key: "reranking_enabled",
		label: "Re-ranker",
		help: "Re-reads the top results; slow on CPU (+2.3 GB).",
		control: { kind: "switch" },
	},
	{
		key: "rerank_provider",
		label: "Re-ranker type",
		help: "Local model, or an LLM using the caller's key.",
		control: {
			kind: "select",
			options: [
				{ value: "local", label: "Local model" },
				{ value: "llm", label: "LLM" },
			],
		},
		dependsOn: { key: "reranking_enabled", value: true },
	},
	{
		key: "rerank_llm_model",
		label: "Re-ranker model",
		help: "Only for LLM re-ranking.",
		control: { kind: "text" },
		dependsOn: { key: "rerank_provider", value: "llm" },
	},
	{
		key: "mmr_enabled",
		label: "Variety (MMR)",
		help: "Pushes near-duplicates down.",
		control: { kind: "switch" },
	},
	{
		key: "mmr_lambda",
		label: "Variety weight",
		help: "1 = pure relevance.",
		control: { kind: "number", min: 0, max: 1, step: 0.05, integer: false },
		dependsOn: { key: "mmr_enabled", value: true },
	},
	{
		key: "temporal_decay_enabled",
		label: "Prefer recent documents",
		help: "Half-life is an engine setting (Engine info).",
		control: { kind: "switch" },
	},
	{
		key: "validation_enabled",
		label: "Answer check",
		help: "Short model calls score confidence.",
		control: { kind: "switch" },
	},
	{
		key: "cache_enabled",
		label: "Answer cache",
		help: "Repeated questions answered instantly.",
		control: { kind: "switch" },
	},
	{
		key: "pipeline_order_enabled",
		label: "Tuned pipeline order",
		help: "Keep on unless debugging.",
		control: { kind: "switch" },
	},
	{
		key: "boilerplate_strip",
		label: "Strip repeated page furniture",
		help: "Drops menus and footers that many pages of the site share.",
		control: { kind: "switch" },
	},
	{
		key: "collapse_duplicates",
		label: "Collapse duplicate passages",
		help: "Shows one passage when several documents hold the same text.",
		control: { kind: "switch" },
	},
] as const satisfies readonly RetrievalFieldDef[];

type Listed = (typeof RETRIEVAL_FIELDS)[number]["key"];
/** Compile-time exhaustiveness: both directions must be `true`. */
type Missing = Exclude<RetrievalKey, Listed>;
type Extra = Exclude<Listed, RetrievalKey>;
export const RETRIEVAL_FIELDS_EXHAUSTIVE: [Missing, Extra] extends [never, never] ? true : false = true;

export const RETRIEVAL_KEYS: readonly RetrievalKey[] = RETRIEVAL_FIELDS.map((f) => f.key);

export type RetrievalValue = boolean | number | string | null;
export type RetrievalValues = Record<RetrievalKey, RetrievalValue>;

/** Stored config (response JSON) → one value per field; missing = null (engine default). */
export function valuesFromConfig(config: Record<string, unknown> | null | undefined): RetrievalValues {
	const out = {} as RetrievalValues;
	for (const f of RETRIEVAL_FIELDS) {
		const v = config?.[f.key];
		out[f.key] = typeof v === "boolean" || typeof v === "number" || typeof v === "string" ? v : null;
	}
	return out;
}

/** GET /info `retrieval_defaults`: what each field resolves to when the corpus stores nothing. */
export type RetrievalDefaults = components["schemas"]["RetrievalDefaultsInfo"];

/**
 * What an unset field actually runs with. The engine resolves a missing field straight to its own
 * settings (a preset is expanded into stored values when chosen; it is not consulted afterwards),
 * so this is `retrieval_defaults[key]`, or null while /info is unknown.
 */
export function engineDefault(key: RetrievalKey, engine: RetrievalDefaults | null | undefined): RetrievalValue {
	const v = engine?.[key];
	return v === undefined ? null : v;
}

/** A field's value in words: on/off, an option's label, a number or a model id. */
export function describeValue(
	key: RetrievalKey,
	v: RetrievalValue,
	opts: { denseWeight?: RetrievalValue; on: string; off: string },
): string | null {
	if (v === null || v === "") return null;
	if (typeof v === "boolean") return v ? opts.on : opts.off;
	const def = RETRIEVAL_FIELDS.find((f) => f.key === key);
	if (def?.control.kind === "select") {
		const label = def.control.options.find((o) => o.value === v)?.label ?? String(v);
		const w = opts.denseWeight;
		return key === "fusion_strategy" && v === "convex" && w !== null && w !== undefined && w !== ""
			? `${label} ${w}`
			: label;
	}
	return String(v);
}

/** The preset whose every value matches the stored config, else null (custom). */
export function matchingPreset(
	config: Record<string, unknown> | null | undefined,
	presets: Record<string, Record<string, unknown>>,
): string | null {
	if (!config) return null;
	for (const [name, values] of Object.entries(presets)) {
		const keys = Object.keys(values);
		if (keys.length && keys.every((k) => config[k] === values[k])) return name;
	}
	return null;
}
