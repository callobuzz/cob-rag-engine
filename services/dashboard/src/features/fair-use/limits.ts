import type { Plan } from "@/lib/api/accounts-types";
import type { components } from "@/lib/api/schema";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { type fairUseStrings, planFairUseStrings as s } from "@/lib/strings/pipelines";

type S = components["schemas"];
export type FairUseLimits = S["FairUseLimits"];
export type FairUsePolicy = S["FairUsePolicy"];
export type LimitField = keyof FairUseLimits;
export type Unit = "count" | "mb" | "usd";
export type Group = keyof typeof fairUseStrings.groups;

export interface LimitDef {
	field: LimitField;
	/** The metric the engine reports this limit under (spend drops the `_usd_micro` suffix). */
	metric: string;
	group: Group;
	unit: Unit;
	max?: number;
}

/** The twelve limits of a business, grouped as the screens show them. */
export const LIMIT_DEFS: LimitDef[] = [
	{ field: "questions_per_minute", metric: "questions_per_minute", group: "questions", unit: "count" },
	{ field: "questions_per_hour", metric: "questions_per_hour", group: "questions", unit: "count" },
	{ field: "questions_per_day", metric: "questions_per_day", group: "questions", unit: "count" },
	{ field: "concurrent_answers", metric: "concurrent_answers", group: "questions", unit: "count" },
	{ field: "website_pages_per_day", metric: "website_pages_per_day", group: "website", unit: "count" },
	{ field: "website_pages_total", metric: "website_pages_total", group: "website", unit: "count" },
	{ field: "fetches_per_day", metric: "fetches_per_day", group: "website", unit: "count" },
	{ field: "uploads_per_day", metric: "uploads_per_day", group: "files", unit: "count" },
	{ field: "storage_mb", metric: "storage_mb", group: "files", unit: "mb" },
	{ field: "ai_spend_per_day_usd_micro", metric: "ai_spend_per_day", group: "spend", unit: "usd" },
	{ field: "ai_spend_per_month_usd_micro", metric: "ai_spend_per_month", group: "spend", unit: "usd" },
	{ field: "cob_questions_per_day", metric: "cob_questions_per_day", group: "cob", unit: "count" },
];

/** Plans keep the website pages total as `website_pages_max` (BIZ1), so the plan editor skips it. */
export const PLAN_LIMIT_DEFS = LIMIT_DEFS.filter((d) => d.field !== "website_pages_total");

export const GROUP_ORDER: Group[] = ["questions", "website", "files", "spend", "cob"];

export const defOf = (field: LimitField): LimitDef => LIMIT_DEFS.find((d) => d.field === field) as LimitDef;

const MICRO = 1_000_000;

/** "12.5" or "12.50" dollars to integer micro-dollars, with no float arithmetic. null = empty, undefined = invalid. */
export function usdToMicro(text: string): number | null | undefined {
	const t = text.replace(/[,\s$]/g, "");
	if (t === "") return null;
	const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(t);
	if (!m) return undefined;
	const micro = Number(m[1]) * MICRO + Number((m[2] ?? "").padEnd(2, "0")) * (MICRO / 100);
	return Number.isSafeInteger(micro) && micro >= 1 ? micro : undefined;
}

/** Integer micro-dollars as the text of a dollar field ("12.5", "0.01"). */
export function microToUsd(micro: number | null | undefined): string {
	if (micro == null) return "";
	const whole = Math.floor(micro / MICRO);
	const frac = String(micro % MICRO)
		.padStart(6, "0")
		.replace(/0+$/, "");
	return frac === "" ? String(whole) : `${whole}.${frac}`;
}

/** A count field: empty = null, whole numbers from 1 (grouping commas allowed), else undefined. */
export function parseCount(text: string): number | null | undefined {
	const t = text.replace(/[,\s_]/g, "");
	if (t === "") return null;
	if (!/^\d+$/.test(t)) return undefined;
	const n = Number(t);
	return Number.isSafeInteger(n) && n >= 1 ? n : undefined;
}

export function parseLimitText(def: LimitDef, text: string): number | null | undefined {
	return def.unit === "usd" ? usdToMicro(text) : parseCount(text);
}

export function limitToText(def: LimitDef, v: number | null | undefined): string {
	if (v == null) return "";
	return def.unit === "usd" ? microToUsd(v) : String(v);
}

// ---- the plan editor's draft ----

export interface PolicyDraft {
	on_billing_block: FairUsePolicy["on_billing_block"];
	on_fair_use_exceeded: FairUsePolicy["on_fair_use_exceeded"];
	downgrade_profile_id: string; // "" = the built-in Free profile
	hard_limit_pct: string;
	alert_thresholds: string; // "80, 100"
}

export interface FairUseDraft {
	profileId: string; // "" = the default profile
	limits: Partial<Record<LimitField, string>>;
	policy: PolicyDraft;
}

export const DEFAULT_POLICY: FairUsePolicy = {
	on_billing_block: "downgrade_profile",
	on_fair_use_exceeded: "throttle_then_stop",
	hard_limit_pct: 150,
	alert_thresholds_pct: [80, 100],
	downgrade_profile_id: null,
};

export function policyDraft(p: Partial<FairUsePolicy> | null | undefined): PolicyDraft {
	const v = { ...DEFAULT_POLICY, ...(p ?? {}) };
	return {
		on_billing_block: v.on_billing_block,
		on_fair_use_exceeded: v.on_fair_use_exceeded,
		downgrade_profile_id: v.downgrade_profile_id ?? "",
		hard_limit_pct: String(v.hard_limit_pct),
		alert_thresholds: (v.alert_thresholds_pct ?? []).join(", "),
	};
}

export function draftFromPlan(plan: Plan | null): FairUseDraft {
	const limits: Partial<Record<LimitField, string>> = {};
	for (const d of PLAN_LIMIT_DEFS) limits[d.field] = limitToText(d, plan?.fair_use_limits?.[d.field]);
	return { profileId: plan?.pipeline_profile_id ?? "", limits, policy: policyDraft(plan?.fair_use_policy) };
}

export function parseThresholds(text: string): number[] | undefined {
	const parts = text
		.split(/[,\s]+/)
		.map((x) => x.trim())
		.filter(Boolean);
	if (parts.length > 5) return undefined;
	const nums = parts.map((p) => (/^\d+$/.test(p) ? Number(p) : Number.NaN));
	if (nums.some((n) => !Number.isInteger(n) || n < 1 || n > 100)) return undefined;
	if (new Set(nums).size !== nums.length) return undefined;
	return nums;
}

export type DraftErrors = Partial<Record<LimitField | "hard_limit_pct" | "alert_thresholds", string>>;

/**
 * A limit's text as a value. A text that still reads as the stored value counts as unchanged and keeps
 * that value as it is, even when it could not be typed in again (a sub-cent spend set through the API).
 */
function parseKeeping(def: LimitDef, text: string, stored: number | null | undefined): number | null | undefined {
	if (stored != null && text.trim() === limitToText(def, stored)) return stored;
	return parseLimitText(def, text);
}

export function validateDraft(d: FairUseDraft, plan?: Plan | null): DraftErrors {
	const e: DraftErrors = {};
	for (const def of PLAN_LIMIT_DEFS) {
		if (parseKeeping(def, d.limits[def.field] ?? "", plan?.fair_use_limits?.[def.field]) === undefined)
			e[def.field] = def.unit === "usd" ? s.spendInvalid : s.limitInvalid;
	}
	const hard = Number(d.policy.hard_limit_pct);
	if (!/^\d+$/.test(d.policy.hard_limit_pct.trim()) || hard < 100 || hard > 1000)
		e.hard_limit_pct = s.happens.hardLineInvalid;
	if (parseThresholds(d.policy.alert_thresholds) === undefined) e.alert_thresholds = s.happens.alertsInvalid;
	return e;
}

export function limitsOf(d: FairUseDraft, plan?: Plan | null): FairUseLimits {
	const out: Record<string, number> = {};
	for (const def of PLAN_LIMIT_DEFS) {
		const v = parseKeeping(def, d.limits[def.field] ?? "", plan?.fair_use_limits?.[def.field]);
		if (v != null) out[def.field] = v;
	}
	return out;
}

export function policyOf(d: PolicyDraft): FairUsePolicy {
	return {
		on_billing_block: d.on_billing_block,
		on_fair_use_exceeded: d.on_fair_use_exceeded,
		downgrade_profile_id: d.downgrade_profile_id === "" ? null : d.downgrade_profile_id,
		hard_limit_pct: Number(d.hard_limit_pct),
		alert_thresholds_pct: parseThresholds(d.alert_thresholds) ?? [],
	};
}

const sameLimits = (a: FairUseLimits, b: FairUseLimits): boolean =>
	PLAN_LIMIT_DEFS.every((d) => (a[d.field] ?? null) === (b[d.field] ?? null));

function samePolicy(a: Partial<FairUsePolicy>, b: Partial<FairUsePolicy>): boolean {
	const x = { ...DEFAULT_POLICY, ...a };
	const y = { ...DEFAULT_POLICY, ...b };
	return (
		x.on_billing_block === y.on_billing_block &&
		x.on_fair_use_exceeded === y.on_fair_use_exceeded &&
		(x.downgrade_profile_id ?? null) === (y.downgrade_profile_id ?? null) &&
		x.hard_limit_pct === y.hard_limit_pct &&
		JSON.stringify(x.alert_thresholds_pct ?? []) === JSON.stringify(y.alert_thresholds_pct ?? [])
	);
}

export interface PlanFairUseBody {
	pipeline_profile_id?: string | null;
	fair_use_limits?: FairUseLimits;
	fair_use_policy?: FairUsePolicy;
}

/**
 * What to add to a plan's create or update body. Only what changed is sent: the engine replaces a
 * plan's whole limits (or policy) object when it is sent, so a changed one goes in full, with every
 * limit still set, and an untouched one is left out. "No limit" is null, and only for a limit
 * that was set before; one that was never set stays absent. A new plan sends only what is set.
 */
export function bodyFromDraft(d: FairUseDraft, plan: Plan | null): PlanFairUseBody {
	const body: PlanFairUseBody = {};
	const profile = d.profileId === "" ? null : d.profileId;
	if (plan ? profile !== (plan.pipeline_profile_id ?? null) : profile !== null) body.pipeline_profile_id = profile;
	const limits: FairUseLimits = limitsOf(d, plan);
	if (plan ? !sameLimits(limits, plan.fair_use_limits ?? {}) : Object.keys(limits).length > 0) {
		// A limit that was set and is now empty is sent as null (the engine reads it as "no limit").
		const full: Record<string, number | null> = { ...limits };
		for (const def of PLAN_LIMIT_DEFS) {
			if (full[def.field] == null && plan?.fair_use_limits?.[def.field] != null) full[def.field] = null;
		}
		body.fair_use_limits = full as FairUseLimits;
	}
	const policy = policyOf(d.policy);
	if (plan ? !samePolicy(policy, plan.fair_use_policy ?? {}) : !samePolicy(policy, DEFAULT_POLICY))
		body.fair_use_policy = policy;
	return body;
}

// ---- reading limits back ----

/** "30 an hour" style phrase for one question limit. */
const QUESTION_PHRASES: [LimitField, keyof typeof s.card][] = [
	["questions_per_minute", "perMinute"],
	["questions_per_hour", "perHour"],
	["questions_per_day", "perDay"],
];

/** "Economy · 30 an hour · 300 a day" for a plan card. */
export function planSpeedLine(plan: Plan): string {
	const label = plan.pipeline_profile_label ?? null;
	const parts = QUESTION_PHRASES.flatMap(([field, key]) => {
		const v = plan.fair_use_limits?.[field];
		return v == null ? [] : [fill(s.card[key], { n: formatNumber(v) })];
	});
	return [label, parts.length ? parts.join(" · ") : s.card.noQuestionLimits].filter(Boolean).join(" · ");
}

/** A limit for reading: "30", "2 GB"-less "500 MB", "$12.50". */
export function formatLimitValue(def: LimitDef, v: number): string {
	if (def.unit === "usd") return `$${microToUsd(v)}`;
	if (def.unit === "mb") return `${formatNumber(v)} MB`;
	return formatNumber(v);
}
