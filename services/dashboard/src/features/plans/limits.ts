import type { Plan, PlanLimits } from "@/lib/api/accounts-types";
import { formatNumber } from "@/lib/format/number";
import { plansStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";

/** The four limits of a plan (A6), in the mockup's order. */
export const LIMIT_KEYS = ["max_knowledge_bases", "monthly_questions", "max_documents", "max_widget_keys"] as const;
export type LimitKey = (typeof LIMIT_KEYS)[number];

/** The two size caps (lower than the engine's; empty = the engine default). */
export const SIZE_KEYS = ["max_upload_mb", "max_document_tokens"] as const;
export type SizeKey = (typeof SIZE_KEYS)[number];

/** "10 MB" / "100,000 tokens" / "Engine default" (null). */
export function formatSizeLimit(k: SizeKey, v: number | null | undefined): string {
	if (v == null) return t.card.engineDefault;
	return k === "max_upload_mb" ? fill(t.card.mb, { n: formatNumber(v) }) : fill(t.card.tokens, { n: formatNumber(v) });
}

/** "1,000" or "Unlimited" (null). */
export function formatLimit(v: number | null | undefined): string {
	return v == null ? t.card.unlimited : formatNumber(v);
}

/**
 * A limit field's text → the API value: empty = null (unlimited); a whole number ≥ 1 (grouping
 * commas and spaces allowed, "10,000"); anything else = undefined (invalid).
 */
export function parseLimit(text: string): number | null | undefined {
	const s = text.replace(/[,\s_]/g, "");
	if (s === "") return null;
	if (!/^\d+$/.test(s)) return undefined;
	const n = Number(s);
	return Number.isSafeInteger(n) && n >= 1 ? n : undefined;
}

/** Limit → the form's text ("" for unlimited). */
export function limitText(v: number | null | undefined): string {
	return v == null ? "" : String(v);
}

/** "1 knowledge base · 1,000 questions a month" / "unlimited" (activate and change-plan pickers). */
export function planSummary(p: PlanLimits): string {
	if (LIMIT_KEYS.every((k) => p[k] == null)) return t.summary.unlimited;
	const kbs =
		p.max_knowledge_bases == null
			? null
			: p.max_knowledge_bases === 1
				? t.summary.kb
				: fill(t.summary.kbs, { n: formatNumber(p.max_knowledge_bases) });
	const q = p.monthly_questions == null ? null : fill(t.summary.questions, { n: formatNumber(p.monthly_questions) });
	return [kbs, q].filter(Boolean).join(" · ") || t.summary.unlimited;
}

/** The plan the engine seeded on install ("Standard"), marked by the API (A8 `is_builtin`). */
export function builtInPlanId(plans: Plan[]): string | null {
	return plans.find((p) => p.is_builtin)?.id ?? null;
}

/** Card order: the default first, then active plans by name, switched-off plans, the locked Internal plan last. */
export function sortPlans(plans: Plan[]): Plan[] {
	const rank = (p: Plan) => (p.is_internal ? 3 : p.is_default ? 0 : p.is_active ? 1 : 2);
	return [...plans].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, "en-IN"));
}
