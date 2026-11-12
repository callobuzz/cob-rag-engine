import { chooseIndex, type Option } from "./select";

/**
 * The pricing calculator: what a number of customer questions costs a month. The same rule as
 * `landing/calc.py` on the same cases (`tests/fixtures/landing/calc_cases.json`): whole minor
 * units only, GST half-up, no floating point in any amount. The page's sentence says "estimated".
 */
export type CalcPlan = {
	key: string;
	fee_minor: number;
	included_credit_minor: number;
	usage_mode: "included" | "per_question" | "cost_plus";
	per_question_minor: number;
	question_cap: number | null;
	overage_charge_minor: number | null;
	est_answer_minor: number | null;
};

export type CalcResult = { key: string; fits: boolean; monthly_minor: number; monthly_incl_minor: number };

/** `monthly x 1.18` rounded half up, in BigInt: a large plan times 11,800 would pass 2^53. */
export function inclusiveTotal(monthly: number, gst: boolean): number {
	if (!gst) return monthly;
	return Number((2n * BigInt(monthly) * 11800n + 10000n) / 20000n);
}

export function monthlyCost(plan: CalcPlan, questions: number, gst: boolean): CalcResult {
	let fits = true;
	let usage = 0;
	if (plan.usage_mode === "per_question") {
		usage = questions * plan.per_question_minor;
	} else if (plan.usage_mode === "cost_plus") {
		if (plan.est_answer_minor === null) fits = false;
		else usage = questions * plan.est_answer_minor;
	}
	let overage = 0;
	if (plan.question_cap !== null && questions > plan.question_cap) {
		if (plan.overage_charge_minor === null) fits = false;
		else overage = (questions - plan.question_cap) * plan.overage_charge_minor;
	}
	const monthly = plan.fee_minor + Math.max(0, usage - plan.included_credit_minor) + overage;
	return { key: plan.key, fits, monthly_minor: monthly, monthly_incl_minor: inclusiveTotal(monthly, gst) };
}

type Ranked = { rank: readonly [number, number, number]; result: CalcResult };

function less(a: readonly number[], b: readonly number[]): boolean {
	for (let i = 0; i < a.length; i++) {
		const x = a[i] as number;
		const y = b[i] as number;
		if (x !== y) return x < y;
	}
	return false;
}

/** The fitting plan with the lowest monthly cost; ties go to the lower fee, then the earlier card. */
export function recommend(plans: readonly CalcPlan[], questions: number, gst: boolean): CalcResult | null {
	let best: Ranked | null = null;
	for (const [index, plan] of plans.entries()) {
		const result = monthlyCost(plan, questions, gst);
		if (!result.fits) continue;
		const rank = [result.monthly_minor, plan.fee_minor, index] as const;
		if (best === null || less(rank, best.rank)) best = { rank, result };
	}
	return best === null ? null : best.result;
}

function group(digits: string, currency: string): string {
	if (digits.length <= 3) return digits;
	if (currency === "INR") {
		let head = digits.slice(0, -3);
		const tail = digits.slice(-3);
		const parts: string[] = [];
		while (head.length > 2) {
			parts.unshift(head.slice(-2));
			head = head.slice(0, -2);
		}
		if (head) parts.unshift(head);
		return [...parts, tail].join(",");
	}
	return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** `₹999`, `₹5,150.83`, `$15`: whole units when there are no paise (the server's `money_text`). */
export function formatMoney(minor: number, currency: string): string {
	const whole = Math.trunc(minor / 100);
	const cents = minor % 100;
	const symbol = currency === "USD" ? "$" : "₹";
	const base = `${symbol}${group(String(whole), currency)}`;
	return cents === 0 ? base : `${base}.${String(cents).padStart(2, "0")}`;
}

export type CalcData = {
	plans: CalcPlan[];
	names: Record<string, string>;
	slider_max: number;
	slider_default: number;
	gst: boolean;
	texts: { result: string; result_gst: string; none: string };
	currency: string;
	period: string;
};

function fill(template: string, values: Record<string, string>): string {
	let out = template;
	for (const [name, value] of Object.entries(values)) out = out.split(`{${name}}`).join(value);
	return out;
}

/** The plans a visitor's currency and period show: one variant per card. */
export function plansFor(all: readonly CalcPlan[], currency: string, period: string): CalcPlan[] {
	const cards = new Map<string, CalcPlan[]>();
	for (const plan of all) {
		const card = plan.key.split(":")[0] as string;
		cards.set(card, [...(cards.get(card) ?? []), plan]);
	}
	const out: CalcPlan[] = [];
	for (const variants of cards.values()) {
		const options: Option[] = variants.map((p) => {
			const [, cur, per] = p.key.split(":");
			return [cur as string, per as string];
		});
		out.push(variants[chooseIndex(options, currency, period)] as CalcPlan);
	}
	return out;
}

export function sentence(data: CalcData, plans: readonly CalcPlan[], questions: number): string {
	const best = recommend(plans, questions, data.gst);
	if (best === null) return data.texts.none;
	const currency = best.key.split(":")[1] as string;
	return fill(data.gst ? data.texts.result_gst : data.texts.result, {
		plan: data.names[best.key] ?? "",
		amount: formatMoney(best.monthly_minor, currency),
		incl: formatMoney(best.monthly_incl_minor, currency),
	});
}

export function initCalc(doc: Document = document): void {
	const root = doc.querySelector<HTMLElement>("[data-calc]");
	const script = doc.getElementById("pricing-data");
	if (!root || !script?.textContent) return;
	let data: CalcData;
	try {
		data = JSON.parse(script.textContent) as CalcData;
	} catch {
		return;
	}
	const range = root.querySelector<HTMLInputElement>("[data-calc-range]");
	const number = root.querySelector<HTMLInputElement>("[data-calc-number]");
	const result = root.querySelector<HTMLElement>("[data-calc-result]");
	if (!range || !number || !result) return;
	const holder = doc.querySelector<HTMLElement>("[data-pricing]");

	const update = (raw: string): void => {
		const parsed = Number.parseInt(raw, 10);
		const questions = Number.isFinite(parsed) ? Math.min(data.slider_max, Math.max(0, parsed)) : 0;
		const currency = holder?.dataset.currency ?? data.currency;
		const period = holder?.dataset.period ?? data.period;
		result.textContent = sentence(data, plansFor(data.plans, currency, period), questions);
		range.value = String(questions);
	};
	range.addEventListener("input", () => {
		number.value = range.value;
		update(range.value);
	});
	number.addEventListener("input", () => update(number.value));
	holder?.addEventListener("pricing:change", () => update(number.value));
	update(number.value);
}
