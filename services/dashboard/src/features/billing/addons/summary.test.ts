import { describe, expect, it } from "vitest";
import type { AddOn, Grant } from "@/lib/api/billing-types";
import { formatMinor } from "@/lib/format/money";
import { addOnSummary, applyEffect, bonusOf, creditTotal, grantMoney, nextMonthStart } from "./summary";

const base: AddOn = {
	id: "a",
	kind: "question_pack",
	name: "x",
	description: null,
	currency: "INR",
	price_minor: 0,
	credit_minor: null,
	bonus_pct: 0,
	expiry_days: null,
	limit_field: null,
	limit_delta: null,
	questions: null,
	duration: null,
	is_active: true,
	feature: null,
	renews: false,
	includes_support_months: null,
	sac_code: null,
	sold_to_owners: false,
	placeholder: false,
	created_at: "2026-10-01T00:00:00Z",
	updated_at: "2026-10-01T00:00:00Z",
};
const NOW = new Date("2026-10-04T10:00:00Z");

describe("credit with a bonus", () => {
	it("rounds the bonus down in whole minor units, like the engine", () => {
		expect(bonusOf(100_000, 10)).toBe(10_000);
		expect(bonusOf(333, 10)).toBe(33);
		expect(creditTotal(100_000, 10)).toBe(110_000);
		expect(creditTotal(100_000, 0)).toBe(100_000);
	});
});

describe("addOnSummary", () => {
	it("describes each kind in one line", () => {
		expect(addOnSummary({ ...base, kind: "credit_pack", credit_minor: 100_000, bonus_pct: 10, expiry_days: 90 })).toBe(
			"₹1,000.00 credit, +10% bonus, expires after 90 days",
		);
		expect(addOnSummary({ ...base, kind: "credit_pack", credit_minor: 50_000 })).toBe("₹500.00 credit");
		expect(addOnSummary({ ...base, questions: 500 })).toBe("+500 questions this month");
		expect(
			addOnSummary({
				...base,
				kind: "extra_limit",
				limit_field: "max_documents",
				limit_delta: 50,
				duration: "period",
			}),
		).toBe("+50 documents, this month");
		expect(
			addOnSummary({
				...base,
				kind: "extra_limit",
				limit_field: "max_widget_keys",
				limit_delta: 1,
				duration: "permanent",
			}),
		).toBe("+1 website widget keys, until revoked");
	});
});

describe("applyEffect", () => {
	it("shows a finite limit before and after, with the month's end for a period pack", () => {
		const pack = {
			...base,
			kind: "extra_limit" as const,
			limit_field: "max_documents" as const,
			limit_delta: 50,
			duration: "period" as const,
		};
		expect(applyEffect(pack, { max_documents: 100 }, NOW)).toEqual({
			text: "Documents 100 → 150 until 1 Nov",
			noChange: false,
		});
		expect(applyEffect({ ...pack, duration: "permanent" }, { max_documents: 100 }, NOW).text).toBe(
			"Documents 100 → 150",
		);
	});

	it("shows a question pack as this month's total", () => {
		expect(applyEffect({ ...base, questions: 500 }, { monthly_questions: 1000 }, NOW)).toEqual({
			text: "Questions a month 1,000 → 1,500 this month",
			noChange: false,
		});
	});

	it("warns that an unlimited base changes nothing", () => {
		const out = applyEffect({ ...base, questions: 500 }, { monthly_questions: null }, NOW);
		expect(out.noChange).toBe(true);
		expect(out.text).toBe("Questions a month is already unlimited, so this changes nothing.");
	});

	it("shows a credit pack's total and its expiry day", () => {
		const out = applyEffect(
			{ ...base, kind: "credit_pack", credit_minor: 100_000, bonus_pct: 10, expiry_days: 90 },
			undefined,
			NOW,
		);
		expect(out.text).toBe("+₹1,100.00 credit, expires 2 Jan");
	});

	it("falls back to the summary while the customer's limits are loading", () => {
		expect(applyEffect({ ...base, questions: 500 }, undefined, NOW).text).toBe("+500 questions this month");
	});
});

describe("nextMonthStart", () => {
	it("is the first of the next UTC month, across a year end", () => {
		expect(nextMonthStart(new Date("2026-10-31T23:59:59Z")).toISOString()).toBe("2026-11-01T00:00:00.000Z");
		expect(nextMonthStart(new Date("2026-12-15T00:00:00Z")).toISOString()).toBe("2027-01-01T00:00:00.000Z");
	});
});

describe("grant history money", () => {
	it("shows a credit pack's credit with its bonus, as the balance got it", () => {
		const grant = {
			kind: "credit_pack",
			currency: "INR",
			credit_minor: 100_000,
			bonus_pct: 10,
			payment: "free",
			price_minor: 0,
		} as unknown as Grant;
		expect(grantMoney(grant)).toBe(formatMinor(110_000, "INR"));
	});
});
