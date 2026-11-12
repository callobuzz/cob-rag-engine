import { queryOptions } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { type Currency, toDisplayAmount } from "@/lib/format/currency";

/** Static benchmark data shipped with the engine image: fetched once, kept for the session. */
export type FastModelResult = components["schemas"]["FastModelResult"];
export type FastModelBenchmarks = components["schemas"]["FastModelBenchmarks"];

const STATIC = 60 * 60_000;

export const fastBenchmarksQuery = queryOptions({
	queryKey: ["models", "fast-benchmarks"] as const,
	queryFn: () => unwrap(api().GET("/api/v1/models/fast-benchmarks")),
	staleTime: STATIC,
});

export const benchmarkSummaryQuery = queryOptions({
	queryKey: ["models", "benchmark-summary"] as const,
	queryFn: () => unwrap(api().GET("/api/v1/models/benchmark-summary")),
	staleTime: STATIC,
});

export const BAR_KEYS = ["overall", "per_language", "business_recall", "reply_language", "fallbacks"] as const;

/** The bars a row misses, in display order. */
export function failedBars(m: FastModelResult): (typeof BAR_KEYS)[number][] {
	return BAR_KEYS.filter((k) => !m.bars[k]);
}

export function sameHosts(a: readonly string[], b: readonly string[]): boolean {
	return a.length === b.length && a.every((h, i) => h === b[i]);
}

/** The benchmark row for a model id (and hosts when several rows share the id). */
export function findResult(
	models: readonly FastModelResult[] | undefined,
	model: string | null | undefined,
	hosts: readonly string[] = [],
): FastModelResult | undefined {
	if (!model || !models) return undefined;
	const same = models.filter((m) => m.model === model);
	return same.find((m) => sameHosts(m.provider_order, hosts)) ?? same[0];
}

export function pct(v: number | null | undefined): string {
	return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

export function seconds(v: number | null | undefined): string {
	return v == null ? "—" : `${v.toFixed(2)} s`;
}

/** "groq, cerebras" -> ["groq","cerebras"] (trimmed, lowercase, no empties, no repeats). */
export function parseHosts(s: string): string[] {
	const out: string[] = [];
	for (const part of s.split(",")) {
		const h = part.trim().toLowerCase();
		if (h && !out.includes(h)) out.push(h);
	}
	return out;
}

export type AnswerBenchmarks = components["schemas"]["AnswerBenchmarks"];
export type AnswerModelResult = components["schemas"]["AnswerModelResult"];
export type AnswerLanguageSlice = components["schemas"]["AnswerLanguageSlice"];

export const answerBenchmarksQuery = queryOptions({
	queryKey: ["models", "answer-benchmarks"] as const,
	queryFn: () => unwrap(api().GET("/api/v1/models/answer-benchmarks")),
	staleTime: STATIC,
});

/** The language filter of the answer table: what the engine measured, plus Hindi and Hinglish together. */
export const LANGUAGE_FILTERS = ["all", "en", "hi", "hi-Latn", "hindi_all"] as const;
export type LanguageFilter = (typeof LANGUAGE_FILTERS)[number];

/** The measures of a row for a language filter; "all" reads the row's own totals. */
export interface Measures {
	pass: number | null;
	noAnswer: number | null;
	tables: number | null;
	ttftMs: number | null;
	usd: number | null;
	naturalness: number | null;
	register: number | null;
	items: number;
}

export function measures(m: AnswerModelResult, lang: LanguageFilter): Measures {
	const hindi = m.by_language.hindi_all;
	if (lang === "all") {
		return {
			pass: m.passed ?? null,
			noAnswer: m.refusal_correct_unanswerable ?? null,
			tables: m.table_rate ?? null,
			ttftMs: m.ttft_p50_ms ?? null,
			usd: m.usd_per_1000_answers ?? null,
			naturalness: hindi?.naturalness ?? null,
			register: hindi?.register_ok ?? null,
			items: m.items,
		};
	}
	const s = m.by_language[lang];
	return {
		pass: s?.passed ?? null,
		noAnswer: s?.refusal_correct_unanswerable ?? null,
		tables: s?.table_rate ?? null,
		ttftMs: s?.ttft_p50_ms ?? null,
		usd: s?.usd_per_1000_answers ?? null,
		naturalness: s?.naturalness ?? null,
		register: s?.register_ok ?? null,
		items: s?.items ?? 0,
	};
}

/** The answer benchmark row for a model id (the first row when several thinking levels share it). */
export function findAnswer(
	models: readonly AnswerModelResult[] | undefined,
	model: string | null | undefined,
): AnswerModelResult | undefined {
	if (!model || !models) return undefined;
	const id = model.trim();
	const same = models.filter((m) => m.model === id);
	return same.find((m) => m.passes) ?? same[0];
}

/** The reasons a Hindi-and-Hinglish reader should hesitate, for a row that meets the overall bar. */
export function hindiCautions(
	m: AnswerModelResult,
	bars: AnswerBenchmarks["bars"],
): ("pass" | "noAnswer" | "register")[] {
	const s = m.by_language.hindi_all;
	if (!s || m.hindi_passes) return [];
	const out: ("pass" | "noAnswer" | "register")[] = [];
	if ((s.passed ?? 0) < bars.hindi_pass_min) out.push("pass");
	if ((s.refusal_correct_unanswerable ?? 0) < bars.hindi_no_answer_min) out.push("noAnswer");
	if ((s.register_ok ?? 0) < bars.hindi_register_min) out.push("register");
	return out;
}

/** "google/gemma-4-31b-it" -> "Gemma 4 31B", "openai/gpt-6-luna" -> "GPT-6 Luna". Display only. */
export function friendlyName(id: string): string {
	const base = (id.split("/").pop() ?? id).split("@")[0] ?? id;
	if (base.startsWith("gpt-oss")) return base;
	const parts = base.split("-").filter((p) => !/^a\d+b$/.test(p) && p !== "it" && p !== "instruct");
	const words: string[] = [];
	for (const p of parts) {
		const prev = words[words.length - 1];
		if (/^\d+b$/i.test(p)) words.push(p.toUpperCase());
		else if (p === "gpt") words.push("GPT");
		else if (prev === "GPT" && /^[\d.]+$/.test(p)) words[words.length - 1] = `GPT-${p}`;
		else words.push(p.charAt(0).toUpperCase() + p.slice(1));
	}
	return words.join(" ");
}

/** A cost per 1,000 in the display currency with one decimal below 100 (₹13.6, ₹4.9, $0.162). */
export function perThousand(usd: number | null | undefined, currency: Currency): string {
	if (usd == null || Number.isNaN(usd)) return "—";
	if (currency === "USD") return `$${usd < 1 ? usd.toFixed(3) : usd.toFixed(2)}`;
	const inr = toDisplayAmount(usd, "INR");
	return `₹${inr < 100 ? inr.toFixed(1) : Math.round(inr).toLocaleString("en-IN")}`;
}

export function ms(v: number | null | undefined): string {
	return v == null ? "—" : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(2)} s`;
}

export function pct0(v: number | null | undefined): string {
	return v == null ? "—" : `${Math.round(v * 100)}%`;
}

/** How much cheaper `to` is than `from`, as a whole percent; null unless it is cheaper. */
export function savingPercent(from: number | null | undefined, to: number | null | undefined): number | null {
	if (from == null || to == null || from <= 0 || to >= from) return null;
	return Math.round((1 - to / from) * 100);
}

/** The budget fast model: the engine's `recommendations.budget` pick, or a row it tagged "budget". */
export function fastBudget(data: FastModelBenchmarks | null | undefined): FastModelResult | undefined {
	if (!data) return undefined;
	const key = (data.recommendations as { budget?: string | null }).budget;
	return (
		data.models.find((m) => m.key === key) ?? data.models.find((m) => (m.tags as readonly string[]).includes("budget"))
	);
}

/** A fast row's badges: its tags, plus Budget when it is the budget pick. */
export function fastTags(data: FastModelBenchmarks | null | undefined, m: FastModelResult): string[] {
	const tags: string[] = [...m.tags];
	if (fastBudget(data)?.key === m.key && !tags.includes("budget")) tags.push("budget");
	return tags;
}
