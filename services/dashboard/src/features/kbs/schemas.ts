import { z } from "zod";
import { fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { RETRIEVAL_FIELDS, type RetrievalKey } from "./retrieval-fields";

/**
 * Form schemas (bounds from docs/openapi.json). Form values are strings for text/number inputs;
 * an empty string means "not set" and is sent as `null` (the engine default applies again).
 */

/** "" | number-like string within bounds. */
function optionalNumber(min: number, max: number, integer: boolean) {
	return z.string().superRefine((raw, ctx) => {
		const v = raw.trim();
		if (v === "") return;
		const n = Number(v);
		if (Number.isNaN(n)) {
			ctx.addIssue({ code: "custom", message: fill(t.retrieval.between, { min, max }) });
			return;
		}
		if (integer && !Number.isInteger(n)) ctx.addIssue({ code: "custom", message: t.retrieval.wholeNumber });
		else if (n < min || n > max) ctx.addIssue({ code: "custom", message: fill(t.retrieval.between, { min, max }) });
	});
}

const maxLen = (max: number) => z.string().max(max, fill(t.prompt.tooLong, { max }));

export const llmConfigSchema = z.object({
	provider: z.string().trim().max(64),
	model: z.string().trim(),
	fast_model: z.string().trim(),
	fast_provider_order: z.string().trim(),
	thinking: z.enum(["", "off", "minimal", "low", "medium", "high", "provider-default"]),
	fast_thinking: z.enum(["", "off", "minimal", "low", "medium", "high", "provider-default"]),
	base_url: z.string().trim(),
	temperature: optionalNumber(0, 2, false),
	max_tokens: optionalNumber(1, 100_000, true),
	lang_mode: z.enum(["default", "custom", "off"]),
	lang_en: z.string().trim(),
	lang_hi: z.string().trim(),
	lang_hi_latn: z.string().trim(),
	api_key: z.string(),
});
export type LlmConfigForm = z.infer<typeof llmConfigSchema>;
export type LangMode = LlmConfigForm["lang_mode"];

export const promptConfigSchema = z.object({
	system_prompt: maxLen(10_000),
	constraints: maxLen(5_000),
	language: z.string(),
	language_other: maxLen(50),
});
export type PromptConfigForm = z.infer<typeof promptConfigSchema>;

/** Validate one advanced field's form value: `true` or the message to show on its row. */
export function validateRetrievalValue(key: RetrievalKey, value: boolean | string | null): true | string {
	const def = RETRIEVAL_FIELDS.find((f) => f.key === key);
	if (def?.control.kind !== "number" || typeof value !== "string") return true;
	const c = def.control;
	const r = optionalNumber(c.min, c.max, c.integer).safeParse(value);
	return r.success || (r.error.issues[0]?.message ?? "Invalid");
}
export type RetrievalConfigForm = Record<RetrievalKey, boolean | string | null>;

export const createKbSchema = z.object({
	name: z.string().trim().min(1, t.create.nameRequired).max(255, t.create.nameLong),
	description: z.string().trim(),
	tenant_id: z.string(),
	preset: z.string(),
	embedding: z.string(),
	provider: z.string().trim().max(64),
	model: z.string().trim(),
	fast_model: z.string().trim(),
	api_key: z.string(),
});
export type CreateKbForm = z.infer<typeof createKbSchema>;

export const moveSchema = z.object({ tenant_id: z.string().min(1, t.danger.pickFirst) });

/** "" → null, else the trimmed number. */
export function numOrNull(v: string): number | null {
	const s = v.trim();
	return s === "" ? null : Number(s);
}

/** "" → null, else the trimmed string. */
export function strOrNull(v: string): string | null {
	const s = v.trim();
	return s === "" ? null : s;
}
