/**
 * The value report part of the fake engine (TV4): the owner's value settings, a month's report with
 * its formula, the frozen months, the printable page and the deployer's view. Follows
 * `api/routes/value.py`: a workspace sign-in or tenant key reads and (owners) writes its own values;
 * the operator has no workspace (403 `no_workspace`); a member's save is 403 `owner_required`.
 * Indian demo data: Shree Dental Clinic, Pune, a clinic (Rs 1,500 a lead, Rs 800 a request to talk to
 * a person, Rs 250 an hour, 3 minutes).
 */

export interface ValueMock {
	saved: Record<string, unknown> | null;
	puts: Record<string, unknown>[];
}

export const createValueState = (): ValueMock => ({ saved: null, puts: [] });

const CURRENT = "2026-10";
const FROZEN = ["2026-09", "2026-08"];
const CLINIC = {
	lead_value_minor: 150_000,
	handoff_value_minor: 80_000,
	staff_hour_minor: 25_000,
	minutes_per_answer: 3,
};

const INPUTS = {
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

function rupees(minor: number): string {
	return `₹${Math.trunc(minor / 100)
		.toString()
		.replace(/(\d)(?=(\d\d)+\d$)/g, "$1,")}`;
}

function report(state: ValueMock, month: string, live: boolean) {
	const v = (state.saved ?? CLINIC) as typeof CLINIC;
	const leads = INPUTS.leads_qualified * v.lead_value_minor;
	const handoffs = INPUTS.handoffs * v.handoff_value_minor;
	const minutes = INPUTS.answered * v.minutes_per_answer;
	const time = Math.floor((minutes * v.staff_hour_minor) / 60);
	return {
		month,
		live,
		currency: "INR",
		saved_settings: state.saved !== null,
		settings_used: {
			lead_value_minor: v.lead_value_minor,
			handoff_value_minor: v.handoff_value_minor,
			staff_hour_minor: v.staff_hour_minor,
			minutes_per_answer: v.minutes_per_answer,
		},
		inputs: INPUTS,
		breakdown: {
			leads_minor: leads,
			handoffs_minor: handoffs,
			minutes_saved: minutes,
			time_minor: time,
			total_minor: leads + handoffs + time,
			formula: [
				{
					key: "leads",
					inputs: { count: INPUTS.leads_qualified, value_minor: v.lead_value_minor },
					amount_minor: leads,
					text: `${INPUTS.leads_qualified} qualified leads × ${rupees(v.lead_value_minor)} = ${rupees(leads)}`,
				},
				{
					key: "handoffs",
					inputs: { count: INPUTS.handoffs, value_minor: v.handoff_value_minor },
					amount_minor: handoffs,
					text: `${INPUTS.handoffs} requests to talk to a person × ${rupees(v.handoff_value_minor)} = ${rupees(handoffs)}`,
				},
				{
					key: "time",
					inputs: { minutes_saved: minutes, staff_hour_minor: v.staff_hour_minor },
					amount_minor: time,
					text: `${INPUTS.answered} questions answered`,
				},
			],
		},
		formula_text: [],
		after_hours_since: null,
	};
}

function settings(state: ValueMock) {
	const v = (state.saved ?? CLINIC) as typeof CLINIC & { email_enabled?: boolean; industry_defaults?: string | null };
	return {
		saved: state.saved !== null,
		currency: "INR",
		business_type: "clinic",
		industry_defaults: state.saved?.industry_defaults ?? null,
		email_enabled: v.email_enabled ?? true,
		lead_value_minor: v.lead_value_minor,
		handoff_value_minor: v.handoff_value_minor,
		staff_hour_minor: v.staff_hour_minor,
		minutes_per_answer: v.minutes_per_answer,
		suggested: CLINIC,
	};
}

/** What Home carries as `value` for a business: month to date, saved or not, last month. */
export function homeValue(state: ValueMock) {
	const r = report(state, CURRENT, true);
	return {
		month_to_date: {
			total_minor: r.breakdown.total_minor,
			currency: "INR",
			answered: INPUTS.answered,
			leads_qualified: INPUTS.leads_qualified,
			handoffs: INPUTS.handoffs,
		},
		saved_settings: state.saved !== null,
		last_month_total_minor: report(state, "2026-09", false).breakdown.total_minor,
	};
}

export interface ValueCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	role: "owner" | "member" | null;
	reply: (status: number, body?: unknown) => Promise<void>;
	html: (body: string) => Promise<void>;
}

function monthReply(state: ValueMock, url: URL, ctx: ValueCtx): Promise<void> {
	const month = url.searchParams.get("month") ?? "";
	if (month && (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month) || month > CURRENT)) {
		return ctx.reply(422, { detail: "That month has not happened yet.", code: "value_month_invalid" });
	}
	if (url.searchParams.get("format") === "html") {
		return ctx.html(
			`<!doctype html><title>Value report</title><h1>What your assistant did</h1><p>Estimated value, based on your settings.</p>`,
		);
	}
	return ctx.reply(200, report(state, month || CURRENT, !month || month === CURRENT));
}

export async function handleValueRoute(
	state: ValueMock,
	method: string,
	url: URL,
	body: unknown,
	ctx: ValueCtx,
): Promise<boolean> {
	const path = url.pathname;
	const admin = /^\/api\/v1\/admin\/tenants\/[^/]+\/value-report$/.test(path);
	if (admin && method === "GET") {
		if (!ctx.isAdmin) {
			await ctx.reply(403, { detail: "Admin only", code: "forbidden" });
			return true;
		}
		await monthReply(state, url, ctx);
		return true;
	}
	if (!path.startsWith("/api/v1/value/")) return false;
	if (ctx.isAdmin || !ctx.tenantId) {
		await ctx.reply(403, { detail: "There is no workspace here.", code: "no_workspace" });
		return true;
	}
	if (method === "GET" && path === "/api/v1/value/settings") {
		await ctx.reply(200, settings(state));
		return true;
	}
	if (method === "PUT" && path === "/api/v1/value/settings") {
		if (ctx.role === "member") {
			await ctx.reply(403, { detail: "Only a workspace owner can do this", code: "owner_required" });
			return true;
		}
		state.puts.push(body as Record<string, unknown>);
		state.saved = body as Record<string, unknown>;
		await ctx.reply(200, settings(state));
		return true;
	}
	if (method === "GET" && path === "/api/v1/value/report") {
		await monthReply(state, url, ctx);
		return true;
	}
	if (method === "GET" && path === "/api/v1/value/reports") {
		await ctx.reply(200, {
			items: FROZEN.map((month) => ({
				month,
				total_minor: report(state, month, false).breakdown.total_minor,
				currency: "INR",
			})),
		});
		return true;
	}
	return false;
}
