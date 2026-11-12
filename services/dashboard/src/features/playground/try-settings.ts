import { z } from "zod";
import type { QueryOptions } from "./api";

/**
 * Try settings (D5): only what `POST /query` accepts per request — `QueryOptions` plus a one-off
 * `system_prompt`. Preset, re-ranker and blend weight are knowledge-base settings (linked, not
 * faked). The zod schema mirrors the request model's bounds and is strict: the engine's model
 * forbids extra fields, so the dashboard cannot build one.
 */
export type TriState = "" | "on" | "off";
export type SearchModeChoice = "" | "dense" | "hybrid" | "keyword";

export interface TryState {
	searchMode: SearchModeChoice;
	topK: string;
	finalK: string;
	hyde: TriState;
	classification: TriState;
	validation: TriState;
	category: string;
	filters: { key: string; value: string }[];
	systemPrompt: string;
}

export const EMPTY_TRY: TryState = {
	searchMode: "",
	topK: "",
	finalK: "",
	hyde: "",
	classification: "",
	validation: "",
	category: "",
	filters: [],
	systemPrompt: "",
};

export const SYSTEM_PROMPT_MAX = 4000;
export const CATEGORY_MAX = 50;

const k = z.number().int().min(1, "At least 1").max(100, "At most 100");

export const queryOptionsSchema = z
	.object({
		search_mode: z.enum(["dense", "hybrid", "keyword"]).optional(),
		top_k: k.optional(),
		final_k: k.optional(),
		enable_hyde: z.boolean().optional(),
		enable_classification: z.boolean().optional(),
		enable_validation: z.boolean().optional(),
		category: z.string().min(1).max(CATEGORY_MAX, `At most ${CATEGORY_MAX} characters`).optional(),
		metadata_filters: z.record(z.string().min(1), z.string()).optional(),
	})
	.strict();

export type TryField = "topK" | "finalK" | "category" | "filters" | "systemPrompt";

export type TryResult =
	| { ok: true; options: QueryOptions; systemPrompt: string | undefined; changed: boolean }
	| { ok: false; errors: Partial<Record<TryField, string>> };

const tri = (v: TriState) => (v === "" ? undefined : v === "on");

function int(v: string): number | undefined | null {
	const t = v.trim();
	if (t === "") return undefined;
	if (!/^\d+$/.test(t)) return null;
	return Number.parseInt(t, 10);
}

/** Try state → a valid `QueryOptions` (only the fields that differ from "saved"). */
export function buildTry(s: TryState): TryResult {
	const errors: Partial<Record<TryField, string>> = {};
	const topK = int(s.topK);
	const finalK = int(s.finalK);
	if (topK === null) errors.topK = "Enter a whole number";
	if (finalK === null) errors.finalK = "Enter a whole number";
	const filters: Record<string, string> = {};
	for (const f of s.filters) {
		const key = f.key.trim();
		if (!key && !f.value.trim()) continue;
		if (!key) {
			errors.filters = "Every filter needs a key";
			continue;
		}
		filters[key] = f.value.trim();
	}
	if (s.systemPrompt.trim().length > SYSTEM_PROMPT_MAX) {
		errors.systemPrompt = `At most ${SYSTEM_PROMPT_MAX.toLocaleString("en-IN")} characters`;
	}
	const candidate: Record<string, unknown> = {
		search_mode: s.searchMode || undefined,
		top_k: topK ?? undefined,
		final_k: finalK ?? undefined,
		enable_hyde: tri(s.hyde),
		enable_classification: tri(s.classification),
		enable_validation: tri(s.validation),
		category: s.category.trim() || undefined,
		metadata_filters: Object.keys(filters).length ? filters : undefined,
	};
	for (const key of Object.keys(candidate)) if (candidate[key] === undefined) delete candidate[key];
	const parsed = queryOptionsSchema.safeParse(candidate);
	if (!parsed.success) {
		for (const issue of parsed.error.issues) {
			const f = issue.path[0];
			if (f === "top_k") errors.topK ??= issue.message;
			else if (f === "final_k") errors.finalK ??= issue.message;
			else if (f === "category") errors.category ??= issue.message;
			else if (f === "metadata_filters") errors.filters ??= issue.message;
		}
	}
	if (Object.keys(errors).length || !parsed.success) return { ok: false, errors };
	const systemPrompt = s.systemPrompt.trim() || undefined;
	return {
		ok: true,
		options: parsed.data as QueryOptions,
		systemPrompt,
		changed: Object.keys(parsed.data).length > 0 || !!systemPrompt,
	};
}
