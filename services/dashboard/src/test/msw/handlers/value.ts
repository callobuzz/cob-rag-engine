import { HttpResponse, http } from "msw";
import type { ValueReport, ValueSettings, ValueSettingsBody } from "@/features/value/api";
import { BASE } from "../data";
import { coded } from "./auth";

/**
 * TV4 handlers: the value settings and report routes, stateful within a test (reset in setup.ts).
 * Shree Dental Clinic, Pune: a clinic, so the suggestions are the clinic's (Rs 1,500 a lead,
 * Rs 800 a request to talk to a person, Rs 250 an hour, 3 minutes). The numbers follow the
 * engine's formula so a test can change the values and watch the estimate move.
 */
export const CURRENT = "2026-10";
export const FROZEN = ["2026-09", "2026-08"];

export const CLINIC = {
	lead_value_minor: 150_000,
	handoff_value_minor: 80_000,
	staff_hour_minor: 25_000,
	minutes_per_answer: 3,
};

interface ValueState {
	saved: ValueSettingsBody | null;
	puts: ValueSettingsBody[];
	/** Months asked for, by the owner's route ("" = the current one). */
	asked: string[];
	adminAsked: { tenant: string; month: string }[];
	/** A test sets what it needs; null = the default numbers. */
	inputs: Partial<ValueReport["inputs"]>;
	afterHoursSince: string | null;
}

export const valueState: ValueState = {
	saved: null,
	puts: [],
	asked: [],
	adminAsked: [],
	inputs: {},
	afterHoursSince: null,
};

export function resetValueState(): void {
	valueState.saved = null;
	valueState.puts = [];
	valueState.asked = [];
	valueState.adminAsked = [];
	valueState.inputs = {};
	valueState.afterHoursSince = null;
}

const BASE_INPUTS: ValueReport["inputs"] = {
	conversations: 188,
	questions: 300,
	answered: 412,
	answered_after_hours: 61,
	leads_total: 12,
	leads_qualified: 9,
	leads_unrated: 0,
	leads_partial: 2,
	handoffs: 23,
	bookings: 14,
	median_first_reply_ms: 800,
	top_questions: [{ question: "root canal ka kitna charge hai?", count: 17 }],
	unanswered: [{ question: "do you take cards", count: 4 }],
};

const money = (minor: number) => {
	const whole = Math.trunc(minor / 100);
	const text = String(whole).replace(/(\d)(?=(\d\d)+\d$)/g, "$1,");
	return `₹${text}`;
};

export function reportFor(month: string, live: boolean): ValueReport {
	const v = valueState.saved ?? { ...CLINIC, email_enabled: true };
	const i = { ...BASE_INPUTS, ...valueState.inputs };
	const leads = i.leads_qualified * v.lead_value_minor;
	const handoffs = i.handoffs * v.handoff_value_minor;
	const minutes = i.answered * v.minutes_per_answer;
	const time = Math.floor((minutes * v.staff_hour_minor) / 60);
	return {
		month,
		live,
		currency: "INR",
		saved_settings: valueState.saved !== null,
		settings_used: {
			lead_value_minor: v.lead_value_minor,
			handoff_value_minor: v.handoff_value_minor,
			staff_hour_minor: v.staff_hour_minor,
			minutes_per_answer: v.minutes_per_answer,
		},
		inputs: i,
		breakdown: {
			leads_minor: leads,
			handoffs_minor: handoffs,
			minutes_saved: minutes,
			time_minor: time,
			total_minor: leads + handoffs + time,
			formula: [
				{
					key: "leads",
					inputs: { count: i.leads_qualified, value_minor: v.lead_value_minor },
					amount_minor: leads,
					text: `${i.leads_qualified} ${i.leads_unrated > 0 ? "leads" : "qualified leads"} × ${money(v.lead_value_minor)} = ${money(leads)}`,
				},
				{
					key: "handoffs",
					inputs: { count: i.handoffs, value_minor: v.handoff_value_minor },
					amount_minor: handoffs,
					text: `${i.handoffs} requests to talk to a person × ${money(v.handoff_value_minor)} = ${money(handoffs)}`,
				},
				{
					key: "time",
					inputs: { minutes_saved: minutes, staff_hour_minor: v.staff_hour_minor },
					amount_minor: time,
					text: `${i.answered} questions answered`,
				},
			],
		},
		formula_text: [],
		after_hours_since: valueState.afterHoursSince,
	};
}

function settingsOut(): ValueSettings {
	const v = valueState.saved;
	return {
		saved: v !== null,
		currency: "INR",
		business_type: "clinic",
		industry_defaults: v?.industry_defaults ?? null,
		email_enabled: v?.email_enabled ?? true,
		...(v
			? {
					lead_value_minor: v.lead_value_minor,
					handoff_value_minor: v.handoff_value_minor,
					staff_hour_minor: v.staff_hour_minor,
					minutes_per_answer: v.minutes_per_answer,
				}
			: CLINIC),
		suggested: CLINIC,
	};
}

function monthParam(request: Request): string {
	return new URL(request.url).searchParams.get("month") ?? "";
}

function reply(request: Request, month: string) {
	const url = new URL(request.url);
	if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month || CURRENT) || (month && month > CURRENT)) {
		return coded(422, "value_month_invalid");
	}
	const report = reportFor(month || CURRENT, !month || month === CURRENT);
	if (url.searchParams.get("format") === "html") {
		return new HttpResponse(
			"<!doctype html><title>Value report</title><p>Estimated value, based on your settings.</p>",
			{
				headers: { "content-type": "text/html; charset=utf-8" },
			},
		);
	}
	return HttpResponse.json(report);
}

export const valueHandlers = [
	http.get(`${BASE}/api/v1/value/settings`, () => HttpResponse.json(settingsOut())),
	http.put(`${BASE}/api/v1/value/settings`, async ({ request }) => {
		const body = (await request.json()) as ValueSettingsBody;
		valueState.puts.push(body);
		valueState.saved = body;
		return HttpResponse.json(settingsOut());
	}),
	http.get(`${BASE}/api/v1/value/report`, ({ request }) => {
		valueState.asked.push(monthParam(request));
		return reply(request, monthParam(request));
	}),
	http.get(`${BASE}/api/v1/value/reports`, () =>
		HttpResponse.json({
			items: FROZEN.map((month) => ({ month, total_minor: 103_333, currency: "INR" })),
		}),
	),
	http.get(`${BASE}/api/v1/admin/tenants/:tenant/value-report`, ({ params, request }) => {
		valueState.adminAsked.push({ tenant: String(params.tenant), month: monthParam(request) });
		return reply(request, monthParam(request));
	}),
];
