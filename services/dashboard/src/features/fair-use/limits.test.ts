import { describe, expect, it } from "vitest";
import type { Plan } from "@/lib/api/accounts-types";
import {
	bodyFromDraft,
	draftFromPlan,
	microToUsd,
	parseThresholds,
	planSpeedLine,
	usdToMicro,
	validateDraft,
} from "./limits";

const plan = (over: Partial<Plan> = {}): Plan =>
	({
		id: "p",
		name: "Free",
		is_internal: false,
		pipeline_profile_id: null,
		pipeline_profile_label: "Economy",
		fair_use_limits: {},
		fair_use_policy: {},
		...over,
	}) as Plan;

describe("dollars and micro-dollars", () => {
	it("12.5 dollars is 12,500,000 micro, an integer, never a float", () => {
		expect(usdToMicro("12.5")).toBe(12_500_000);
		expect(usdToMicro("12.50")).toBe(12_500_000);
		expect(usdToMicro("0.01")).toBe(10_000);
		expect(usdToMicro("1,200")).toBe(1_200_000_000);
		expect(usdToMicro("$3")).toBe(3_000_000);
	});

	it("empty is no limit; more than two decimals, negatives and words are invalid", () => {
		expect(usdToMicro("")).toBeNull();
		expect(usdToMicro("1.234")).toBeUndefined();
		expect(usdToMicro("-1")).toBeUndefined();
		expect(usdToMicro("abc")).toBeUndefined();
		expect(usdToMicro("0")).toBeUndefined();
	});

	it("reads micro back as dollars", () => {
		expect(microToUsd(12_500_000)).toBe("12.5");
		expect(microToUsd(10_000)).toBe("0.01");
		expect(microToUsd(3_000_000)).toBe("3");
		expect(microToUsd(null)).toBe("");
	});
});

describe("what a plan sends", () => {
	it("sends nothing for fair use when nothing changed", () => {
		const p = plan({ fair_use_limits: { questions_per_hour: 30 } });
		expect(bodyFromDraft(draftFromPlan(p), p)).toEqual({});
	});

	it("a changed limit goes in full, a limit cleared after being set goes as null, a never-set one stays out", () => {
		const p = plan({ fair_use_limits: { questions_per_hour: 30, questions_per_day: 300 } });
		const d = draftFromPlan(p);
		d.limits.questions_per_day = "";
		d.limits.ai_spend_per_day_usd_micro = "12.5";
		const body = bodyFromDraft(d, p);
		expect(body.fair_use_limits).toEqual({
			questions_per_hour: 30,
			questions_per_day: null,
			ai_spend_per_day_usd_micro: 12_500_000,
		});
		expect(body.fair_use_limits).not.toHaveProperty("questions_per_minute");
		expect(body.fair_use_policy).toBeUndefined();
		expect(body.pipeline_profile_id).toBeUndefined();
	});

	it("a new plan sends only what is set", () => {
		expect(bodyFromDraft(draftFromPlan(null), null)).toEqual({});
		const d = draftFromPlan(null);
		d.limits.questions_per_hour = "30";
		d.profileId = "pf-free";
		expect(bodyFromDraft(d, null)).toEqual({
			pipeline_profile_id: "pf-free",
			fair_use_limits: { questions_per_hour: 30 },
		});
	});

	it("going back to the default profile sends null", () => {
		const p = plan({ pipeline_profile_id: "pf-free" });
		const d = draftFromPlan(p);
		d.profileId = "";
		expect(bodyFromDraft(d, p)).toEqual({ pipeline_profile_id: null });
	});

	it("a changed policy goes in full", () => {
		const p = plan();
		const d = draftFromPlan(p);
		d.policy.on_billing_block = "pause_handoff";
		d.policy.alert_thresholds = "50, 90, 100";
		expect(bodyFromDraft(d, p).fair_use_policy).toEqual({
			on_billing_block: "pause_handoff",
			on_fair_use_exceeded: "throttle_then_stop",
			downgrade_profile_id: null,
			hard_limit_pct: 150,
			alert_thresholds_pct: [50, 90, 100],
		});
	});
});

describe("checks", () => {
	it("flags bad limits, spend with too many decimals, and bad levels", () => {
		const d = draftFromPlan(null);
		d.limits.questions_per_hour = "ten";
		d.limits.ai_spend_per_day_usd_micro = "1.234";
		d.policy.hard_limit_pct = "50";
		d.policy.alert_thresholds = "80, 80";
		const e = validateDraft(d);
		expect(e.questions_per_hour).toBeDefined();
		expect(e.ai_spend_per_day_usd_micro).toMatch(/2 decimals/);
		expect(e.hard_limit_pct).toBeDefined();
		expect(e.alert_thresholds).toBeDefined();
		expect(validateDraft(draftFromPlan(null))).toEqual({});
	});

	it("a sub-cent spend limit set through the API does not stop an unrelated save", () => {
		const plan = {
			fair_use_limits: { ai_spend_per_day_usd_micro: 12_345_678 },
			pipeline_profile_id: null,
		} as never;
		const d = draftFromPlan(plan);
		expect(d.limits.ai_spend_per_day_usd_micro).toBe("12.345678");
		expect(validateDraft(d, plan)).toEqual({});
		d.limits.questions_per_hour = "40";
		const body = bodyFromDraft(d, plan);
		expect(body.fair_use_limits).toEqual({ ai_spend_per_day_usd_micro: 12_345_678, questions_per_hour: 40 });
		// Typing a new sub-cent amount is still refused.
		d.limits.ai_spend_per_day_usd_micro = "12.3456789";
		expect(validateDraft(d, plan).ai_spend_per_day_usd_micro).toMatch(/2 decimals/);
	});

	it("alert levels are unique numbers from 1 to 100, at most five", () => {
		expect(parseThresholds("80, 100")).toEqual([80, 100]);
		expect(parseThresholds("0")).toBeUndefined();
		expect(parseThresholds("101")).toBeUndefined();
		expect(parseThresholds("1,2,3,4,5,6")).toBeUndefined();
		expect(parseThresholds("")).toEqual([]);
	});
});

describe("the plan card line", () => {
	it("reads Economy, 30 an hour, 300 a day", () => {
		expect(planSpeedLine(plan({ fair_use_limits: { questions_per_hour: 30, questions_per_day: 300 } }))).toBe(
			"Economy · 30 an hour · 300 a day",
		);
		expect(planSpeedLine(plan({ pipeline_profile_label: "Premium" }))).toBe("Premium · no question limits");
	});
});
