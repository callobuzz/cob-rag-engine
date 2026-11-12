import type { components } from "@/lib/api/schema";

/**
 * Cost per answer: the engine reports what the request really cost (`cost_usd` / `cost_known` in
 * `token_usage`: the provider's own figure, helper calls included). Only an answer without those
 * (an older engine) falls back to tokens × the model's catalog prices (`GET /api/v1/models`,
 * `input_cost_per_1m` / `output_cost_per_1m`, USD), shown as an estimate. No price at all is
 * "price unknown", never ₹0.00.
 */
export type ModelEntry = components["schemas"]["ModelEntry"];

export interface TokenCounts {
	prompt: number | null;
	completion: number | null;
	total: number | null;
	/** What the engine says the whole request cost, USD (null: no price). Present only when it reported one. */
	costUsd?: number | null;
	/** False when some call of the request had no price. Present only when the engine reported it. */
	costKnown?: boolean;
}

/** One answer's cost as shown: `usd` null = price unknown; `estimated` = from the catalog, not the engine. */
export interface AnswerCost {
	usd: number | null;
	known: boolean;
	estimated: boolean;
}

/** The engine's figure when `tokens` carries one, else the catalog estimate (`estimateUsd`), else unknown. */
export function answerCost(tokens: TokenCounts | null, estimateUsd: number | null): AnswerCost {
	if (tokens && typeof tokens.costKnown === "boolean") {
		return { usd: tokens.costUsd ?? null, known: tokens.costKnown && tokens.costUsd != null, estimated: false };
	}
	return estimateUsd == null
		? { usd: null, known: false, estimated: false }
		: { usd: estimateUsd, known: true, estimated: true };
}

/** The catalog entry for a model id as the engine reports it (exact, or "<provider>/<id>"). */
export function findModel(catalog: readonly ModelEntry[] | undefined, model: string | null | undefined) {
	if (!catalog || !model) return undefined;
	return catalog.find((m) => m.id === model) ?? catalog.find((m) => `${m.provider}/${m.id}` === model);
}

export function costUsd(
	catalog: readonly ModelEntry[] | undefined,
	model: string | null | undefined,
	tokens: TokenCounts | null,
): number | null {
	const entry = findModel(catalog, model);
	if (!entry || !tokens || tokens.prompt == null || tokens.completion == null) return null;
	return (tokens.prompt * entry.input_cost_per_1m + tokens.completion * entry.output_cost_per_1m) / 1_000_000;
}

/** `token_usage` objects from /query and /query/{id}: {prompt_tokens, completion_tokens, total_tokens}. */
export function tokensFrom(usage: Record<string, unknown> | null | undefined): TokenCounts | null {
	if (!usage) return null;
	const n = (k: string) => (typeof usage[k] === "number" ? (usage[k] as number) : null);
	const prompt = n("prompt_tokens");
	const completion = n("completion_tokens");
	const total = n("total_tokens") ?? (prompt != null && completion != null ? prompt + completion : null);
	if (prompt == null && completion == null && total == null) return null;
	const counts: TokenCounts = { prompt, completion, total };
	if (typeof usage.cost_known === "boolean") {
		counts.costKnown = usage.cost_known;
		counts.costUsd = typeof usage.cost_usd === "number" ? usage.cost_usd : null;
	}
	return counts;
}
