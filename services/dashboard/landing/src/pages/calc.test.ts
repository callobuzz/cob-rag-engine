import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
	type CalcData,
	type CalcPlan,
	formatMoney,
	inclusiveTotal,
	monthlyCost,
	plansFor,
	recommend,
	sentence,
} from "./calc";

type Case = {
	name: string;
	plans: CalcPlan[];
	questions: number;
	gst: boolean;
	expect: { key: string | null; monthly_minor?: number; incl?: number };
};

const cases = JSON.parse(
	readFileSync(path.resolve(import.meta.dirname, "../../../../../tests/fixtures/landing/calc_cases.json"), "utf8"),
) as Case[];

describe("the shared cases (the same file the Python tests read)", () => {
	for (const c of cases) {
		it(c.name, () => {
			const got = recommend(c.plans, c.questions, c.gst);
			expect(got?.key ?? null).toBe(c.expect.key);
			if (got) {
				expect(got.monthly_minor).toBe(c.expect.monthly_minor);
				expect(got.monthly_incl_minor).toBe(c.expect.incl);
			}
		});
	}
});

describe("safe integers", () => {
	it("the largest slider value times the largest price stays below 2^53", () => {
		const worst: CalcPlan = {
			key: "0:INR:monthly",
			fee_minor: 10 ** 12,
			included_credit_minor: 0,
			usage_mode: "per_question",
			per_question_minor: 10 ** 9,
			question_cap: 1,
			overage_charge_minor: 10 ** 9,
			est_answer_minor: null,
		};
		const result = monthlyCost(worst, 1_000_000, false);
		expect(Number.isSafeInteger(result.monthly_minor)).toBe(true);
		// the GST step multiplies by 11,800, so it runs in BigInt
		expect(Number.isSafeInteger(inclusiveTotal(result.monthly_minor, true))).toBe(true);
	});
});

describe("money text", () => {
	it("groups rupees the Indian way and dollars the western way", () => {
		expect(formatMoney(99900, "INR")).toBe("₹999");
		expect(formatMoney(117882, "INR")).toBe("₹1,178.82");
		expect(formatMoney(123456700, "INR")).toBe("₹12,34,567");
		expect(formatMoney(150000, "USD")).toBe("$1,500");
		expect(formatMoney(0, "INR")).toBe("₹0");
	});
});

describe("the sentence", () => {
	const plans = cases[2]?.plans as CalcPlan[];
	const data: CalcData = {
		plans,
		names: { "1:INR:monthly": "Standard", "2:INR:monthly": "Pro" },
		slider_max: 5000,
		slider_default: 900,
		gst: true,
		texts: {
			result: "{plan}: about {amount}.",
			result_gst: "{plan}: about {amount}, {incl} with GST.",
			none: "None fits.",
		},
		currency: "INR",
		period: "monthly",
	};
	it("names the plan and both amounts, and says when nothing fits", () => {
		expect(sentence(data, plans, 1000)).toBe("Standard: about ₹2,499, ₹2,948.82 with GST.");
		const capped: CalcPlan[] = [{ ...(plans[0] as CalcPlan), question_cap: 1, overage_charge_minor: null }];
		expect(sentence(data, capped, 50)).toBe("None fits.");
	});
	it("shows one variant per card for the visitor's currency and period", () => {
		const all: CalcPlan[] = [
			{ ...(plans[0] as CalcPlan), key: "0:INR:monthly" },
			{ ...(plans[0] as CalcPlan), key: "0:INR:yearly" },
			{ ...(plans[0] as CalcPlan), key: "1:USD:monthly" },
		];
		expect(plansFor(all, "INR", "yearly").map((p) => p.key)).toEqual(["0:INR:yearly", "1:USD:monthly"]);
		expect(plansFor(all, "INR", "monthly").map((p) => p.key)).toEqual(["0:INR:monthly", "1:USD:monthly"]);
	});
});
