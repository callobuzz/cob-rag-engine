import type { Plan } from "@/lib/api/accounts-types";
import { customerLimitsStrings as t } from "@/lib/strings/pipelines";
import type { FairUseAdminView, OverridesBody } from "./api";
import {
	DEFAULT_POLICY,
	type FairUsePolicy,
	formatLimitValue,
	LIMIT_DEFS,
	type LimitDef,
	type LimitField,
	limitToText,
	parseLimitText,
	parseThresholds,
} from "./limits";

/** One limit of one business: follow the plan, an own value, or no limit for this business. */
export type Mode = "follow" | "custom" | "none";
export interface LimitRow {
	mode: Mode;
	text: string;
}
export type LimitRows = Record<LimitField, LimitRow>;

export interface PolicyOverrides {
	on_billing_block: string; // "" = follow the plan
	on_fair_use_exceeded: string; // "" = follow the plan
	downgrade_profile_id: string; // "" = follow the plan, FREE = the built-in Free profile, else a profile id
	hard_limit_pct: string; // "" = follow the plan
	alert_thresholds: string; // "" = follow the plan
}

export const FREE = "__free";
export const POLICY_KEYS = [
	"on_billing_block",
	"on_fair_use_exceeded",
	"downgrade_profile_id",
	"hard_limit_pct",
	"alert_thresholds_pct",
] as const;

/** What the plan itself says for a limit (the website pages total is the plan's `website_pages_max`). */
export function planLimit(plan: Plan | undefined, field: LimitField): number | null {
	if (!plan) return null;
	if (field === "website_pages_total") return plan.website_pages_max ?? null;
	return plan.fair_use_limits?.[field] ?? null;
}

/** "30 an hour", "500 MB", "$12.5 a day". */
export function limitPhrase(def: LimitDef, v: number): string {
	const unit = t.limits.unit[def.field] ?? "";
	return [formatLimitValue(def, v), unit].filter(Boolean).join(" ");
}

export function initialRows(view: FairUseAdminView): LimitRows {
	const own = new Set(view.overridden);
	const rows = {} as LimitRows;
	for (const def of LIMIT_DEFS) {
		const v = view.limits[def.field] ?? null;
		rows[def.field] = !own.has(def.field)
			? { mode: "follow", text: limitToText(def, v) }
			: v == null
				? { mode: "none", text: "" }
				: { mode: "custom", text: limitToText(def, v) };
	}
	return rows;
}

export function initialPolicy(view: FairUseAdminView): PolicyOverrides {
	const own = new Set(view.overridden);
	const p = view.policy;
	return {
		on_billing_block: own.has("on_billing_block") ? p.on_billing_block : "",
		on_fair_use_exceeded: own.has("on_fair_use_exceeded") ? p.on_fair_use_exceeded : "",
		downgrade_profile_id: own.has("downgrade_profile_id") ? (p.downgrade_profile_id ?? FREE) : "",
		hard_limit_pct: own.has("hard_limit_pct") ? String(p.hard_limit_pct) : "",
		alert_thresholds: own.has("alert_thresholds_pct") ? (p.alert_thresholds_pct ?? []).join(", ") : "",
	};
}

export function planPolicy(plan: Plan | undefined): FairUsePolicy {
	return { ...DEFAULT_POLICY, ...(plan?.fair_use_policy ?? {}) };
}

export type OverrideErrors = Partial<Record<LimitField | "hard_limit_pct" | "alert_thresholds", string>>;

export interface Built {
	body: OverridesBody;
	errors: OverrideErrors;
	empty: boolean;
}

/**
 * The PUT body for what the deployer changed: an own value goes in `limits` (null = no limit for
 * this business), going back to the plan goes in `clear`, and untouched rows are not sent.
 */
export function buildOverrides(
	before: { rows: LimitRows; policy: PolicyOverrides },
	after: { rows: LimitRows; policy: PolicyOverrides },
	messages: { limit: (def: LimitDef) => string; hardLine: string; alerts: string },
): Built {
	const limits: Record<string, number | null> = {};
	const policy: Record<string, unknown> = {};
	const clear: string[] = [];
	const errors: OverrideErrors = {};

	for (const def of LIMIT_DEFS) {
		const was = before.rows[def.field];
		const now = after.rows[def.field];
		if (now.mode === "follow") {
			if (was.mode !== "follow") clear.push(def.field);
		} else if (now.mode === "none") {
			if (was.mode !== "none") limits[def.field] = null;
		} else {
			// An own value that was not touched is not parsed again: a sub-cent spend set through the API
			// cannot be typed in, and must not stop the deployer saving something else.
			if (was.mode === "custom" && was.text.trim() === now.text.trim()) continue;
			const v = parseLimitText(def, now.text);
			if (v == null) errors[def.field] = messages.limit(def);
			else limits[def.field] = v;
		}
	}

	const b = before.policy;
	const a = after.policy;
	const text = (key: "on_billing_block" | "on_fair_use_exceeded") => {
		if (a[key] === b[key]) return;
		if (a[key] === "") clear.push(key);
		else policy[key] = a[key];
	};
	text("on_billing_block");
	text("on_fair_use_exceeded");
	if (a.downgrade_profile_id !== b.downgrade_profile_id) {
		if (a.downgrade_profile_id === "") clear.push("downgrade_profile_id");
		else policy.downgrade_profile_id = a.downgrade_profile_id === FREE ? null : a.downgrade_profile_id;
	}
	if (a.hard_limit_pct !== b.hard_limit_pct) {
		const n = Number(a.hard_limit_pct);
		if (a.hard_limit_pct.trim() === "") clear.push("hard_limit_pct");
		else if (!/^\d+$/.test(a.hard_limit_pct.trim()) || n < 100 || n > 1000) errors.hard_limit_pct = messages.hardLine;
		else policy.hard_limit_pct = n;
	}
	if (a.alert_thresholds !== b.alert_thresholds) {
		if (a.alert_thresholds.trim() === "") clear.push("alert_thresholds_pct");
		else {
			const nums = parseThresholds(a.alert_thresholds);
			if (nums === undefined || nums.length === 0) errors.alert_thresholds = messages.alerts;
			else policy.alert_thresholds_pct = nums;
		}
	}

	const body: OverridesBody = {};
	if (Object.keys(limits).length) body.limits = limits;
	if (Object.keys(policy).length) body.policy = policy;
	if (clear.length) body.clear = clear;
	return { body, errors, empty: Object.keys(body).length === 0 };
}
