import { fill } from "@/lib/strings/en";
import { plural, pipelinesStrings as t } from "@/lib/strings/pipelines";
import type { Profile, ProfileConfig, ProfileResolved } from "./api";

/** The profile editor's form (text for numbers so a half-typed value is not lost). */
export interface ProfileForm {
	name: string;
	label: string;
	description: string;
	route: "engine" | "local";
	answerModel: string;
	thinking: string;
	languageMode: "follow" | "none" | "custom";
	languages: Record<string, string>;
	fastModel: string;
	fastThinking: string;
	reranker: ProfileConfig["reranker"];
	rerankModel: string;
	topK: string;
	finalK: string;
	rewrites: string;
	hyde: "" | "on" | "off";
	classify: "" | "on" | "off";
	maxAnswer: string;
	followups: boolean;
	priority: ProfileConfig["priority"];
	concurrency: string;
	overrides: boolean;
}

export type FieldKey =
	| "name"
	| "label"
	| "answerModel"
	| "languages"
	| "rerankModel"
	| "topK"
	| "finalK"
	| "rewrites"
	| "maxAnswer"
	| "concurrency"
	| "overrides";

export const LANGUAGE_CODES = ["en", "hi", "hi-Latn"] as const;

const num = (v: number | null | undefined) => (v == null ? "" : String(v));
const tri = (v: boolean | null | undefined): "" | "on" | "off" => (v == null ? "" : v ? "on" : "off");

export function emptyForm(): ProfileForm {
	return {
		name: "",
		label: "",
		description: "",
		route: "engine",
		answerModel: "",
		thinking: "",
		languageMode: "follow",
		languages: {},
		fastModel: "",
		fastThinking: "",
		reranker: "inherit",
		rerankModel: "",
		topK: "",
		finalK: "",
		rewrites: "",
		hyde: "",
		classify: "",
		maxAnswer: "",
		followups: true,
		priority: "normal",
		concurrency: "",
		overrides: false,
	};
}

export function formFromProfile(p: Profile): ProfileForm {
	const c = p.config;
	const lm = c.language_models;
	return {
		name: p.name,
		label: p.label,
		description: p.description ?? "",
		route: c.answer_route,
		answerModel: c.answer_model ?? "",
		thinking: c.thinking ?? "",
		languageMode: lm == null ? "follow" : Object.keys(lm).length === 0 ? "none" : "custom",
		languages: { ...(lm ?? {}) },
		fastModel: c.fast_model ?? "",
		fastThinking: c.fast_thinking ?? "",
		reranker: c.reranker,
		rerankModel: c.rerank_model ?? "",
		topK: num(c.top_k),
		finalK: num(c.final_k),
		rewrites: num(c.multi_query_count),
		hyde: tri(c.hyde_enabled),
		classify: tri(c.query_classification_enabled),
		maxAnswer: num(c.max_answer_tokens),
		followups: c.followups,
		priority: c.priority,
		concurrency: num(c.max_concurrency),
		overrides: c.overrides_kb_settings,
	};
}

/** The local route always replaces the knowledge base's settings (the engine refuses otherwise). */
export function withRoute(f: ProfileForm, route: ProfileForm["route"]): ProfileForm {
	return route === "local" ? { ...f, route, overrides: true } : { ...f, route };
}

function int(text: string, min: number, max: number): number | null | undefined {
	const s = text.replace(/[,\s]/g, "");
	if (s === "") return null;
	if (!/^\d+$/.test(s)) return undefined;
	const n = Number(s);
	return n >= min && n <= max ? n : undefined;
}

const triBool = (v: "" | "on" | "off"): boolean | null => (v === "" ? null : v === "on");
const orNull = (s: string): string | null => (s.trim() === "" ? null : s.trim());

/** The form as the engine's `config` (every optional field present, null = follow). */
export function configFromForm(f: ProfileForm): ProfileConfig {
	const languageModels =
		f.languageMode === "follow"
			? null
			: f.languageMode === "none"
				? {}
				: Object.fromEntries(
						LANGUAGE_CODES.map((c) => [c, (f.languages[c] ?? "").trim()] as const).filter(([, m]) => m !== ""),
					);
	return {
		answer_route: f.route,
		answer_model: orNull(f.answerModel),
		thinking: (f.thinking || null) as ProfileConfig["thinking"],
		language_models: languageModels,
		fast_model: orNull(f.fastModel),
		fast_thinking: (f.fastThinking || null) as ProfileConfig["fast_thinking"],
		reranker: f.reranker,
		rerank_model: f.reranker === "llm" || f.reranker === "api" ? orNull(f.rerankModel) : null,
		top_k: int(f.topK, 1, 100) ?? null,
		final_k: int(f.finalK, 1, 30) ?? null,
		multi_query_count: int(f.rewrites, 0, 5) ?? null,
		hyde_enabled: triBool(f.hyde),
		query_classification_enabled: triBool(f.classify),
		max_answer_tokens: int(f.maxAnswer, 64, 8192) ?? null,
		followups: f.followups,
		priority: f.priority,
		max_concurrency: int(f.concurrency, 1, 200) ?? null,
		overrides_kb_settings: f.route === "local" ? true : f.overrides,
	};
}

/** The same rules the engine applies, so a deployer sees the problem before saving. */
export function validateForm(f: ProfileForm): Partial<Record<FieldKey, string>> {
	const d = t.dialog;
	const e: Partial<Record<FieldKey, string>> = {};
	if (f.name.trim() === "") e.name = d.nameRequired;
	else if (f.name.trim().length > 60) e.name = d.nameLong;
	if (f.label.trim() === "") e.label = d.labelRequired;
	else if (f.label.trim().length > 40) e.label = d.labelLong;
	const bad = (text: string, min: number, max: number) => int(text, min, max) === undefined;
	if (bad(f.topK, 1, 100)) e.topK = fill(d.rangeInt, { min: 1, max: 100 });
	if (bad(f.finalK, 1, 30)) e.finalK = fill(d.rangeInt, { min: 1, max: 30 });
	if (bad(f.rewrites, 0, 5)) e.rewrites = fill(d.rangeInt, { min: 0, max: 5 });
	if (bad(f.maxAnswer, 64, 8192)) e.maxAnswer = d.maxAnswerRange;
	if (bad(f.concurrency, 1, 200)) e.concurrency = d.concurrencyRange;
	const top = int(f.topK, 1, 100);
	const fin = int(f.finalK, 1, 30);
	if (!e.finalK && top != null && fin != null && fin > top) e.finalK = d.finalKTooBig;
	if (f.reranker === "api" && f.rerankModel.trim() === "") e.rerankModel = d.rerankModelRequired;
	return e;
}

/** Which field an `invalid_profile` sentence is about (the engine names the setting). */
export function fieldForServerDetail(detail: string): FieldKey | null {
	const d = detail.toLowerCase();
	if (d.includes("rerank_model")) return "rerankModel";
	if (d.includes("final_k")) return "finalK";
	if (d.includes("top_k")) return "topK";
	if (d.includes("local model") || d.includes("instead of each knowledge base")) return "overrides";
	if (d.includes("language")) return "languages";
	if (d.includes("max_answer_tokens")) return "maxAnswer";
	if (d.includes("max_concurrency")) return "concurrency";
	if (d.includes("multi_query_count")) return "rewrites";
	if (d.includes("answer_model")) return "answerModel";
	return null;
}

export function shortModel(id: string | null | undefined): string {
	if (!id) return t.dialog.nowNothing;
	const i = id.lastIndexOf("/");
	return i >= 0 ? id.slice(i + 1) : id;
}

/** "gpt-oss-120b, low thinking" for the follow-the-engine hint on the model fields. */
export function nowModel(r: ProfileResolved | undefined): string {
	if (!r) return t.dialog.nowNothing;
	const think = r.thinking && r.thinking !== "provider-default" ? `, ${r.thinking} thinking` : "";
	return `${shortModel(r.answer_model)}${think}`;
}

export function nowLanguages(r: ProfileResolved | undefined): string {
	const entries = Object.entries(r?.language_models ?? {});
	if (entries.length === 0) return t.dialog.nowNothing;
	return entries.map(([c, m]) => `${t.dialog.languageRows[c] ?? c}: ${shortModel(m)}`).join(", ");
}

export function nowTri(v: boolean | undefined): string {
	return v == null ? t.dialog.nowNothing : v ? t.dialog.triOn.toLowerCase() : t.dialog.triOff.toLowerCase();
}

/** "2 plans and 1 business" (null when nothing uses it). */
export function usedByText(plans: number, businesses: number): string | null {
	const parts = [
		plans > 0 ? plural(t.card.plans, plans) : null,
		businesses > 0 ? plural(t.card.businesses, businesses) : null,
	].filter((x): x is string => x !== null);
	return parts.length ? parts.join(" and ") : null;
}
