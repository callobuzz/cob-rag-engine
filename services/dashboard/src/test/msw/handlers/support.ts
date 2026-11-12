import { HttpResponse, http } from "msw";
import type { SupportRequestDetail } from "@/features/support/api";
import type { AdminSupportDetail, AdminSupportRequest } from "@/features/support-admin/api";
import { BASE, SHREE } from "../data";
import { coded } from "./auth";

/**
 * TV3 handlers: support requests (the business's side and the provider's inbox), the price list and
 * its public link, and the owner's spending cap. Stateful within a test (reset in setup.ts), with
 * the engine's rules: ten new requests a day, the provider's switch (`support_disabled`), a closed
 * request is opened again by a follow-up, the first reply fixes the first-response time, a cap above
 * the provider's ceiling is `spend_cap_too_high`. Indian demo data: Shree Labs writes to Sahayak AI.
 */
interface Row {
	id: string;
	tenant_id: string;
	workspace_name: string;
	number: number;
	subject: string;
	status: "open" | "answered" | "waiting" | "closed";
	page: string | null;
	context: Record<string, unknown>;
	created_by_email: string;
	created_at: string;
	last_message_at: string;
	first_response_at: string | null;
	closed_at: string | null;
	messages: {
		id: string;
		author_kind: "business" | "deployer";
		author_email: string;
		body: string;
		created_at: string;
	}[];
}

export const REQUEST_ID = "99999999-9999-4999-8999-999999999991";

function seedRow(): Row {
	return {
		id: REQUEST_ID,
		tenant_id: SHREE.id,
		workspace_name: SHREE.name,
		number: 1041,
		subject: "Invoice shows the wrong GST number",
		status: "answered",
		page: "/plan",
		context: { plan: "Pro", workspace_status: "active", paused: false, kb_count: 2, page: "/plan", client_errors: [] },
		created_by_email: "priya@shree-labs.in",
		created_at: "2026-10-03T09:00:00Z",
		last_message_at: "2026-10-03T11:00:00Z",
		first_response_at: "2026-10-03T11:00:00Z",
		closed_at: null,
		messages: [
			{
				id: "m1",
				author_kind: "business",
				author_email: "priya@shree-labs.in",
				body: "The GST number on our October statement is wrong.",
				created_at: "2026-10-03T09:00:00Z",
			},
			{
				id: "m2",
				author_kind: "deployer",
				author_email: "admin key",
				body: "Thanks Priya, we have corrected it.",
				created_at: "2026-10-03T11:00:00Z",
			},
		],
	};
}

export const supportState = {
	rows: [seedRow()],
	enabled: true,
	slaHours: 4 as number | null,
	nextNumber: 1042,
	sent: [] as { method: string; path: string; body: unknown }[],
	/** New requests already sent today, so a test can reach the daily limit. */
	todayCount: 0,
};

export const sheetState = {
	token: null as string | null,
	plan_ids: [] as string[],
	show_add_ons: true,
	note: "",
	rotations: 0,
	removals: 0,
};

export const capState = {
	cap_minor: null as number | null,
	used_minor: 120_000,
	max_minor: 500_000 as number | null,
	puts: [] as (number | null)[],
};

export function resetSupportState(): void {
	supportState.rows = [seedRow()];
	supportState.enabled = true;
	supportState.slaHours = 4;
	supportState.nextNumber = 1042;
	supportState.sent = [];
	supportState.todayCount = 0;
	sheetState.token = null;
	sheetState.plan_ids = [];
	sheetState.show_add_ons = true;
	sheetState.note = "";
	sheetState.rotations = 0;
	sheetState.removals = 0;
	capState.cap_minor = null;
	capState.used_minor = 120_000;
	capState.max_minor = 500_000;
	capState.puts = [];
}

const tenantRow = (r: Row) => ({
	id: r.id,
	number: r.number,
	subject: r.subject,
	status: r.status,
	page: r.page,
	created_by_email: r.created_by_email,
	created_at: r.created_at,
	last_message_at: r.last_message_at,
	first_response_at: r.first_response_at,
	closed_at: r.closed_at,
});

const detailFor = (r: Row): SupportRequestDetail => ({
	...tenantRow(r),
	messages: r.messages.map((m) => ({
		id: m.id,
		author_kind: m.author_kind,
		author: m.author_kind === "business" ? m.author_email : null,
		body: m.body,
		created_at: m.created_at,
	})),
});

const adminRow = (r: Row): AdminSupportRequest => ({
	...tenantRow(r),
	tenant_id: r.tenant_id,
	workspace_name: r.workspace_name,
	source: "landing_contact" in r.context ? "website" : "workspace",
});
const adminDetail = (r: Row): AdminSupportDetail => ({
	...adminRow(r),
	context: r.context,
	messages: r.messages,
});

const byId = (id: string) => supportState.rows.find((r) => r.id === id);
let clock = 0;
const now = () => new Date(Date.UTC(2026, 9, 5, 9, 0, clock++)).toISOString();

function median(values: number[]): number | null {
	if (!values.length) return null;
	const s = [...values].sort((a, b) => a - b);
	return s[Math.floor(s.length / 2)] ?? null;
}

const SHEET_PLANS = [
	{ name: "Starter", price: "₹499 a month" },
	{ name: "Growth", price: "₹999 a month" },
];

export const supportHandlers = [
	// ------------------------------------------------------- the business's side
	http.get(`${BASE}/api/v1/support/info`, () =>
		HttpResponse.json({
			enabled: supportState.enabled,
			provider_name: "Sahayak AI",
			sla_hours: supportState.slaHours,
			open_count: supportState.rows.filter((r) => r.tenant_id === SHREE.id && r.status !== "closed").length,
		}),
	),
	http.get(`${BASE}/api/v1/support/requests`, ({ request }) => {
		const status = new URL(request.url).searchParams.get("status");
		const items = supportState.rows
			.filter((r) => r.tenant_id === SHREE.id && (!status || r.status === status))
			.sort((a, b) => b.last_message_at.localeCompare(a.last_message_at))
			.map(tenantRow);
		return HttpResponse.json({ items, total: items.length });
	}),
	http.post(`${BASE}/api/v1/support/requests`, async ({ request }) => {
		const body = (await request.json()) as {
			subject: string;
			message: string;
			page?: string;
			client_errors?: string[];
		};
		supportState.sent.push({ method: "POST", path: "requests", body });
		if (!supportState.enabled) return coded(409, "support_disabled");
		if (supportState.todayCount >= 10)
			return HttpResponse.json(
				{ detail: "You have sent the most requests allowed in a day.", code: "support_rate_limited" },
				{ status: 429, headers: { "Retry-After": "3600" } },
			);
		supportState.todayCount += 1;
		const at = now();
		const row: Row = {
			id: `99999999-9999-4999-8999-${String(supportState.nextNumber).padStart(12, "0")}`,
			tenant_id: SHREE.id,
			workspace_name: SHREE.name,
			number: supportState.nextNumber++,
			subject: body.subject,
			status: "open",
			page: body.page ?? null,
			context: {
				plan: "Pro",
				workspace_status: "active",
				paused: false,
				kb_count: 2,
				page: body.page ?? null,
				client_errors: body.client_errors ?? [],
			},
			created_by_email: "priya@shree-labs.in",
			created_at: at,
			last_message_at: at,
			first_response_at: null,
			closed_at: null,
			messages: [
				{
					id: `m-${clock}`,
					author_kind: "business",
					author_email: "priya@shree-labs.in",
					body: body.message,
					created_at: at,
				},
			],
		};
		supportState.rows.unshift(row);
		return HttpResponse.json(tenantRow(row), { status: 201 });
	}),
	http.get(`${BASE}/api/v1/support/requests/:id`, ({ params }) => {
		const row = byId(String(params.id));
		return row && row.tenant_id === SHREE.id
			? HttpResponse.json(detailFor(row))
			: coded(404, "support_request_not_found");
	}),
	http.post(`${BASE}/api/v1/support/requests/:id/messages`, async ({ params, request }) => {
		const row = byId(String(params.id));
		if (!row || row.tenant_id !== SHREE.id) return coded(404, "support_request_not_found");
		const body = (await request.json()) as { body: string };
		supportState.sent.push({ method: "POST", path: `requests/${row.id}/messages`, body });
		if (!supportState.enabled) return coded(409, "support_disabled");
		const at = now();
		row.messages.push({
			id: `m-${clock}`,
			author_kind: "business",
			author_email: "priya@shree-labs.in",
			body: body.body,
			created_at: at,
		});
		row.status = "open";
		row.closed_at = null;
		row.last_message_at = at;
		return HttpResponse.json(detailFor(row).messages.at(-1), { status: 201 });
	}),
	http.post(`${BASE}/api/v1/support/requests/:id/close`, ({ params }) => {
		const row = byId(String(params.id));
		if (!row || row.tenant_id !== SHREE.id) return coded(404, "support_request_not_found");
		supportState.sent.push({ method: "POST", path: `requests/${row.id}/close`, body: null });
		row.status = "closed";
		row.closed_at = now();
		return HttpResponse.json(tenantRow(row));
	}),
	// ------------------------------------------------------- the provider's inbox
	http.get(`${BASE}/api/v1/admin/support/requests`, ({ request }) => {
		const u = new URL(request.url);
		const status = u.searchParams.get("status");
		const q = (u.searchParams.get("q") ?? "").trim().replace(/^#/, "").toLowerCase();
		const items = supportState.rows
			.filter((r) => !status || r.status === status)
			.filter(
				(r) =>
					!q ||
					String(r.number) === q ||
					r.subject.toLowerCase().includes(q) ||
					r.workspace_name.toLowerCase().includes(q),
			)
			.sort((a, b) => b.last_message_at.localeCompare(a.last_message_at))
			.map(adminRow);
		return HttpResponse.json({ items, total: items.length });
	}),
	http.get(`${BASE}/api/v1/admin/support/stats`, () => {
		const waits = supportState.rows
			.filter((r) => r.first_response_at)
			.map((r) => Math.floor((Date.parse(r.first_response_at as string) - Date.parse(r.created_at)) / 60_000));
		const within = waits.filter((w) => supportState.slaHours != null && w <= supportState.slaHours * 60).length;
		return HttpResponse.json({
			open: supportState.rows.filter((r) => r.status === "open").length,
			answered: waits.length,
			median_first_response_minutes: median(waits),
			within_sla_pct: supportState.slaHours != null && waits.length ? Math.floor((within * 100) / waits.length) : null,
			sla_hours: supportState.slaHours,
			days: 30,
		});
	}),
	http.get(`${BASE}/api/v1/admin/support/requests/:id`, ({ params }) => {
		const row = byId(String(params.id));
		return row ? HttpResponse.json(adminDetail(row)) : coded(404, "support_request_not_found");
	}),
	http.post(`${BASE}/api/v1/admin/support/requests/:id/messages`, async ({ params, request }) => {
		const row = byId(String(params.id));
		if (!row) return coded(404, "support_request_not_found");
		const body = (await request.json()) as { body: string; status?: Row["status"] };
		supportState.sent.push({ method: "POST", path: `admin/requests/${row.id}/messages`, body });
		const at = now();
		row.messages.push({
			id: `m-${clock}`,
			author_kind: "deployer",
			author_email: "admin key",
			body: body.body,
			created_at: at,
		});
		row.first_response_at ??= at;
		row.status = body.status ?? "answered";
		row.closed_at = row.status === "closed" ? at : null;
		row.last_message_at = at;
		return HttpResponse.json(adminDetail(row), { status: 201 });
	}),
	http.patch(`${BASE}/api/v1/admin/support/requests/:id`, async ({ params, request }) => {
		const row = byId(String(params.id));
		if (!row) return coded(404, "support_request_not_found");
		const body = (await request.json()) as { status: Row["status"] };
		supportState.sent.push({ method: "PATCH", path: `admin/requests/${row.id}`, body });
		row.status = body.status;
		row.closed_at = body.status === "closed" ? now() : null;
		return HttpResponse.json(adminDetail(row));
	}),
	// ------------------------------------------------------- the price list
	http.get(`${BASE}/api/v1/admin/price-sheet`, ({ request }) => {
		if (new URL(request.url).searchParams.get("format") === "html") {
			return new HttpResponse(`<!doctype html><title>Price list</title><h1>Price list</h1><p>${sheetState.note}</p>`, {
				headers: { "content-type": "text/html; charset=utf-8" },
			});
		}
		return HttpResponse.json({
			product_name: "Sahayak AI",
			note: sheetState.note,
			generated_on: "5 October 2026",
			plans: SHEET_PLANS.map((p) => ({
				...p,
				description: "",
				credit: null,
				usage: "₹2 per question",
				beyond_limit: null,
				limits: [{ label: "Questions each month", value: "Unlimited" }],
				upcoming: null,
			})),
			add_ons: [],
		});
	}),
	http.post(`${BASE}/api/v1/admin/price-sheet/link`, () => {
		sheetState.rotations += 1;
		sheetState.token = `tok${sheetState.rotations}`;
		return HttpResponse.json(
			{ url: `https://api.sahayak.example/api/v1/public/price-sheet/${sheetState.token}` },
			{ status: 201 },
		);
	}),
	http.delete(`${BASE}/api/v1/admin/price-sheet/link`, () => {
		sheetState.removals += 1;
		sheetState.token = null;
		return new HttpResponse(null, { status: 204 });
	}),
	// ------------------------------------------------------- the owner's spending cap
	http.put(`${BASE}/api/v1/billing/spend-cap`, async ({ request }) => {
		const body = (await request.json()) as { cap_minor: number | null };
		capState.puts.push(body.cap_minor);
		if (body.cap_minor != null && capState.max_minor != null && body.cap_minor > capState.max_minor)
			return coded(422, "spend_cap_too_high", { max_minor: capState.max_minor });
		capState.cap_minor = body.cap_minor;
		const state =
			body.cap_minor == null
				? "ok"
				: capState.used_minor >= body.cap_minor
					? "reached"
					: capState.used_minor * 100 >= body.cap_minor * 80
						? "near"
						: "ok";
		return HttpResponse.json({
			cap_minor: body.cap_minor,
			used_minor: capState.used_minor,
			max_minor: capState.max_minor,
			state,
		});
	}),
];
