import type { AddOn, AddOnKind, Grant } from "@/lib/api/billing-types";
import { type Currency, formatMinor } from "@/lib/format/money";
import { addOnStrings as s } from "@/lib/strings/billing-addons";
import { fill } from "@/lib/strings/en";

export const KINDS: readonly AddOnKind[] = [
	"credit_pack",
	"extra_limit",
	"question_pack",
	"feature_unlock", // P5 plans
	"service", // P5 plans
];

/** The most credit a pack can carry, mirroring `bonus_pct` (whole percent) in the engine. */
export function bonusOf(creditMinor: number, pct: number): number {
	return Math.floor((creditMinor * pct) / 100);
}

/** The credit a customer receives: the credit plus its bonus (`billing/addons.py`). */
export function creditTotal(creditMinor: number, pct: number): number {
	return creditMinor + bonusOf(creditMinor, pct);
}

type Shape = Pick<
	AddOn,
	"kind" | "currency" | "credit_minor" | "bonus_pct" | "expiry_days" | "limit_field" | "limit_delta" | "questions"
> & {
	duration?: AddOn["duration"];
	// P5 plans
	feature?: AddOn["feature"];
	renews?: boolean;
	includes_support_months?: number | null;
};

/** One line of what an add-on gives: "₹1,000.00 credit, +10% bonus, expires after 90 days". */
export function addOnSummary(a: Shape): string {
	// P5 plans
	if (a.kind === "feature_unlock") {
		const what = a.duration === "permanent" ? s.summary.byokPermanent : s.summary.byokPeriod;
		return a.renews ? fill(s.summary.renews, { what }) : what;
	}
	if (a.kind === "service") {
		const n = a.includes_support_months ?? 0;
		const what =
			n === 0
				? s.summary.service
				: n === 1
					? s.summary.serviceSupportOne
					: fill(s.summary.serviceSupport, { n: String(n) });
		return a.renews ? fill(s.summary.renews, { what }) : what;
	}
	// /P5 plans
	if (a.kind === "credit_pack") {
		const parts = [fill(s.summary.credit, { credit: formatMinor(a.credit_minor ?? 0, a.currency as Currency) })];
		if (a.bonus_pct) parts.push(fill(s.summary.bonus, { pct: String(a.bonus_pct) }));
		if (a.expiry_days) parts.push(fill(s.summary.expiry, { days: String(a.expiry_days) }));
		return parts.join(", ");
	}
	if (a.kind === "question_pack") return fill(s.summary.questions, { n: String(a.questions ?? 0) });
	const field = a.limit_field ? s.limits[a.limit_field].toLowerCase() : "";
	return fill(a.duration === "permanent" ? s.summary.limitPermanent : s.summary.limitPeriod, {
		n: String(a.limit_delta ?? 0),
		field,
	});
}

/** The day an add-on given today ends: the first of next month (UTC), as the engine computes it. */
export function nextMonthStart(now: Date): Date {
	return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
}

export interface Effect {
	/** Human line for the Apply dialog. */
	text: string;
	/** The base limit is unlimited: the add-on changes nothing. */
	noChange: boolean;
}

export type LimitNumbers = Partial<Record<keyof typeof s.limits, number | null>>;

function day(d: Date): string {
	return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

/**
 * What applying `a` does, in one line. `limits` are the customer's effective limits now (null =
 * unlimited): "Documents 100 → 150 until 1 Nov". On an unlimited base nothing changes.
 */
export function applyEffect(a: AddOn, limits: LimitNumbers | undefined, now: Date = new Date()): Effect {
	const t = s.apply;
	// P5 plans: a feature unlock has no limit to raise, a service opens an order
	if (a.kind === "feature_unlock") {
		const until = a.duration === "period" ? fill(t.effectUntil, { date: day(nextMonthStart(now)) }) : "";
		return { text: fill(t.effectUnlock, { what: addOnSummary(a), until }), noChange: false };
	}
	if (a.kind === "service") return { text: fill(t.effectService, { what: addOnSummary(a) }), noChange: false };
	// /P5 plans
	if (a.kind === "credit_pack") {
		const total = creditTotal(a.credit_minor ?? 0, a.bonus_pct);
		const expiry = a.expiry_days
			? fill(t.effectExpiry, { date: day(new Date(now.getTime() + a.expiry_days * 86_400_000)) })
			: "";
		return {
			text: fill(t.effectCredit, { amount: formatMinor(total, a.currency as Currency), expiry }),
			noChange: false,
		};
	}
	const field = a.kind === "question_pack" ? "monthly_questions" : (a.limit_field ?? "max_documents");
	const base = limits?.[field];
	const label = s.limits[field];
	if (base === null) return { text: fill(t.effectUnlimited, { field: label }), noChange: true };
	const delta = a.kind === "question_pack" ? (a.questions ?? 0) : (a.limit_delta ?? 0);
	if (base === undefined) return { text: addOnSummary(a), noChange: false };
	const from = base.toLocaleString("en-US");
	const to = (base + delta).toLocaleString("en-US");
	if (a.kind === "question_pack") return { text: fill(t.effectQuestions, { from, to }), noChange: false };
	const until = a.duration === "period" ? fill(t.effectUntil, { date: day(nextMonthStart(now)) }) : "";
	return { text: fill(t.effectLimit, { field: label, from, to, until }), noChange: false };
}

/** A grant's money, for the history table: what was added (credit pack) or paid (wallet). */
export function grantMoney(g: Grant): string | null {
	if (g.kind === "credit_pack" && g.credit_minor != null)
		return formatMinor(creditTotal(g.credit_minor, g.bonus_pct), g.currency);
	if (g.payment === "wallet" && g.price_minor > 0) return formatMinor(g.price_minor, g.currency);
	return null;
}
