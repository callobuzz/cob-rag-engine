import type { PricingFields } from "@/lib/api/billing-types";
import { bpToPercent, type Currency, formatMinor, minorToInput, parseMajor, parsePercent } from "@/lib/format/money";
import { customerBillingStrings as cs, billingStrings as s } from "@/lib/strings/billing";
import { fill } from "@/lib/strings/en";

export type PricingKey = Exclude<keyof PricingFields, "currency">;
export type FieldKind = "money" | "bp" | "int" | "bool" | "select" | "thresholds";
export type GroupId = "subscription" | "usage" | "byok" | "overage" | "payment" | "credit" | "alerts" | "members";

/** What a form holds for one field: text for typed values, a boolean for a checkbox. */
export type FormValue = string | boolean;
export type FormValues = Record<PricingKey, FormValue>;

export interface FieldDef {
	key: PricingKey;
	kind: FieldKind;
	group: GroupId;
	/** select: the allowed values. */
	options?: readonly string[];
	/** money: largest minor amount; int: largest number. */
	max?: number;
	/** Whether the field applies given the other values (hidden fields keep their value). */
	showWhen?: (v: FormValues) => boolean;
}

const BIG = 10 ** 12;
const PER_QUESTION = 10 ** 9;

export const GROUPS: readonly GroupId[] = [
	"subscription",
	"usage",
	"byok",
	"overage",
	"payment",
	"credit",
	"alerts",
	"members",
];

export const FIELDS: readonly FieldDef[] = [
	{ key: "subscription_fee_minor", kind: "money", group: "subscription", max: BIG },
	{ key: "billing_period", kind: "select", group: "subscription", options: ["monthly", "yearly"] },
	{ key: "trial_days", kind: "int", group: "subscription", max: 365 },
	{ key: "usage_mode", kind: "select", group: "usage", options: ["included", "per_question", "cost_plus"] },
	{
		key: "per_question_minor",
		kind: "money",
		group: "usage",
		max: PER_QUESTION,
		showWhen: (v) => v.usage_mode === "per_question",
	},
	{ key: "markup_bp", kind: "bp", group: "usage", max: 100_000, showWhen: (v) => v.usage_mode === "cost_plus" },
	{ key: "byok_mode", kind: "select", group: "byok", options: ["free", "per_question"] },
	{
		key: "byok_per_question_minor",
		kind: "money",
		group: "byok",
		max: PER_QUESTION,
		showWhen: (v) => v.byok_mode === "per_question",
	},
	{ key: "byok_counts_toward_caps", kind: "bool", group: "byok" },
	{ key: "overage", kind: "select", group: "overage", options: ["block", "charge", "allow"] },
	{
		key: "overage_mode",
		kind: "select",
		group: "overage",
		options: ["per_question", "cost_plus"],
		showWhen: (v) => v.overage === "charge",
	},
	{
		key: "overage_per_question_minor",
		kind: "money",
		group: "overage",
		max: PER_QUESTION,
		showWhen: (v) => v.overage === "charge" && v.overage_mode === "per_question",
	},
	{ key: "payment_type", kind: "select", group: "payment", options: ["prepaid", "postpaid"] },
	{
		key: "credit_limit_minor",
		kind: "money",
		group: "payment",
		max: BIG,
		showWhen: (v) => v.payment_type === "postpaid",
	},
	{
		key: "grace_minor",
		kind: "money",
		group: "payment",
		max: BIG,
		showWhen: (v) => v.payment_type === "prepaid",
	},
	{ key: "included_credit_minor", kind: "money", group: "credit", max: BIG },
	{ key: "included_credit_expires", kind: "bool", group: "credit" },
	{ key: "alert_thresholds_pct", kind: "thresholds", group: "alerts" },
	{ key: "members_see_money", kind: "bool", group: "members" },
];

export const fieldDef = (key: PricingKey): FieldDef => {
	const d = FIELDS.find((f) => f.key === key);
	if (!d) throw new Error(`unknown pricing field ${key}`);
	return d;
};

export const fieldLabel = (key: PricingKey): string => s.fields[key].label;

/** The defaults of a plan with no pricing: nothing charged, nobody paused (mirrors `PricingFields()`). */
export const PRICING_DEFAULTS: PricingFields = {
	currency: "INR",
	subscription_fee_minor: 0,
	billing_period: "monthly",
	trial_days: 0,
	usage_mode: "included",
	per_question_minor: 0,
	markup_bp: 0,
	byok_mode: "free",
	byok_per_question_minor: 0,
	byok_counts_toward_caps: true,
	overage: "block",
	overage_mode: "per_question",
	overage_per_question_minor: 0,
	payment_type: "prepaid",
	credit_limit_minor: 0,
	grace_minor: 0,
	included_credit_minor: 0,
	included_credit_expires: true,
	alert_thresholds_pct: [20, 5, 0],
	members_see_money: false,
};

type Value = PricingFields[PricingKey];

/** A stored value as what a form field shows. */
export function toInput(def: FieldDef, value: Value | undefined): FormValue {
	if (def.kind === "bool") return value === true;
	if (def.kind === "money") return typeof value === "number" ? minorToInput(value) : "0.00";
	if (def.kind === "bp") return typeof value === "number" ? bpToPercent(value) : "0";
	if (def.kind === "int") return typeof value === "number" ? String(value) : "0";
	if (def.kind === "thresholds") return Array.isArray(value) ? value.join(", ") : "";
	return typeof value === "string" ? value : (def.options?.[0] ?? "");
}

export function toFormValues(fields: PricingFields): FormValues {
	return Object.fromEntries(FIELDS.map((d) => [d.key, toInput(d, fields[d.key])])) as FormValues;
}

export type Parsed = { ok: true; value: Value } | { ok: false; error: string };

/** What a person typed in one field, validated and converted to what the API takes. */
export function parseInput(def: FieldDef, raw: FormValue, currency: Currency): Parsed {
	const e = s.errors;
	switch (def.kind) {
		case "bool":
			return { ok: true, value: raw === true };
		case "select":
			return typeof raw === "string" && def.options?.includes(raw)
				? { ok: true, value: raw as Value }
				: { ok: false, error: e.select };
		case "money": {
			const minor = typeof raw === "string" ? parseMajor(raw, currency) : null;
			if (minor == null || minor < 0) return { ok: false, error: e.money };
			return minor > (def.max ?? BIG) ? { ok: false, error: e.moneyMax } : { ok: true, value: minor };
		}
		case "bp": {
			const bp = typeof raw === "string" ? parsePercent(raw) : null;
			if (bp == null) return { ok: false, error: e.percent };
			return bp > (def.max ?? 100_000) ? { ok: false, error: e.percentMax } : { ok: true, value: bp };
		}
		case "int": {
			const text = typeof raw === "string" ? raw.trim() : "";
			if (!/^\d{1,9}$/.test(text)) return { ok: false, error: e.int };
			const n = Number(text);
			return n > (def.max ?? 0)
				? { ok: false, error: fill(e.intMax, { max: String(def.max ?? 0) }) }
				: { ok: true, value: n };
		}
		case "thresholds": {
			const text = typeof raw === "string" ? raw.trim() : "";
			const parts = text === "" ? [] : text.split(/[\s,]+/).filter(Boolean);
			const nums = parts.map((p) => (/^\d{1,3}$/.test(p) ? Number(p) : Number.NaN));
			const valid =
				nums.length <= 5 &&
				nums.every((n) => Number.isInteger(n) && n >= 0 && n <= 100) &&
				nums.every((n, i) => i === 0 || (nums[i - 1] ?? 0) > n);
			return valid ? { ok: true, value: nums } : { ok: false, error: e.thresholds };
		}
	}
}

/**
 * All of a form's values as a full pricing body, plus the first error per field. A field that does
 * not apply right now (a price while charging is off) is not validated; it keeps its default.
 */
export function toRequest(
	values: FormValues,
	currency: Currency,
): { body: PricingFields; errors: Partial<Record<PricingKey, string>> } {
	const errors: Partial<Record<PricingKey, string>> = {};
	const out: Record<string, unknown> = { currency };
	for (const d of FIELDS) {
		const parsed = parseInput(d, values[d.key], currency);
		if (parsed.ok) out[d.key] = parsed.value;
		else if (d.showWhen && !d.showWhen(values)) out[d.key] = PRICING_DEFAULTS[d.key];
		else {
			errors[d.key] = parsed.error;
			out[d.key] = PRICING_DEFAULTS[d.key];
		}
	}
	return { body: out as unknown as PricingFields, errors };
}

/** A value as plain text for a table or a "Plan: …" hint. */
export function displayValue(def: FieldDef, value: Value | undefined, currency: Currency): string {
	const o = s.options as Record<string, Record<string, string>>;
	switch (def.kind) {
		case "money":
			return typeof value === "number" ? formatMinor(value, currency) : "—";
		case "bp":
			return typeof value === "number" ? fill(cs.fieldValues.percent, { pct: bpToPercent(value) }) : "—";
		case "int":
			return typeof value === "number" ? fill(cs.fieldValues.days, { n: String(value) }) : "—";
		case "bool":
			return value === true ? cs.fieldValues.yes : cs.fieldValues.no;
		case "thresholds":
			return Array.isArray(value) && value.length ? value.map((n) => `${n}%`).join(", ") : cs.fieldValues.none;
		case "select":
			return typeof value === "string" ? (o[def.key]?.[value] ?? value) : "—";
	}
}

const money = (n: number, c: Currency) => formatMinor(n, c);

/** "₹999 / month · ₹2 per question · own key free": the plan's pricing in one line. */
export function pricingSummary(p: PricingFields, currency: Currency = p.currency): string {
	const parts: string[] = [];
	if (p.subscription_fee_minor > 0) {
		parts.push(
			`${money(p.subscription_fee_minor, currency)} / ${p.billing_period === "yearly" ? s.pricing.perYear : s.pricing.perMonth}`,
		);
	}
	if (p.usage_mode === "per_question")
		parts.push(fill(s.pricing.perQuestion, { price: money(p.per_question_minor, currency) }));
	else if (p.usage_mode === "cost_plus") parts.push(fill(s.pricing.aiCostPlus, { pct: bpToPercent(p.markup_bp) }));
	else if (p.subscription_fee_minor > 0) parts.push(s.pricing.questionsIncluded);
	const charges = parts.length > 0 || p.overage === "charge";
	if (p.overage === "charge") {
		parts.push(
			p.overage_mode === "cost_plus"
				? `${fill(s.pricing.aiCostPlus, { pct: bpToPercent(p.markup_bp) })} past the limit`
				: `${fill(s.pricing.perQuestion, { price: money(p.overage_per_question_minor, currency) })} past the limit`,
		);
	}
	if (charges) {
		parts.push(
			p.byok_mode === "per_question"
				? fill(s.pricing.ownKeyPerQuestion, { price: money(p.byok_per_question_minor, currency) })
				: s.pricing.ownKeyFree,
		);
	}
	return parts.length ? parts.join(" · ") : s.pricing.summaryNone;
}

export interface Preview {
	questions: number;
	/** Parts of the bill in order: subscription, then questions. */
	fee: number;
	usage: { kind: "none" } | { kind: "amount"; minor: number } | { kind: "aiCost"; bp: number };
	/** The total in minor units when it can be computed (everything but real AI cost). */
	total: number | null;
}

/** What a customer on the operator's key pays for `questions` questions in a period, within the limit. */
export function previewFor(p: PricingFields, questions: number): Preview {
	const fee = p.subscription_fee_minor;
	if (p.usage_mode === "per_question") {
		const minor = p.per_question_minor * questions;
		return { questions, fee, usage: { kind: "amount", minor }, total: fee + minor };
	}
	if (p.usage_mode === "cost_plus") return { questions, fee, usage: { kind: "aiCost", bp: p.markup_bp }, total: null };
	return { questions, fee, usage: { kind: "none" }, total: fee };
}
